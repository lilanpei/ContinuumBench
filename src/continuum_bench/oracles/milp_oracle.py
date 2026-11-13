"""Solver-backed MILP placement oracle.

The ContinuumBench paper uses this oracle only as a placement reference: the placement-only
``milp`` controller invokes it with ``objective="latency"`` and the MILP backend (SciPy/HiGHS)
plus a no-good-cut feasibility audit against ECLYPSE. The energy and reliability objective terms
and the enumeration backend are auxiliary capabilities exercised by the unit tests.

Knob interface (``continuum_bench.theta``), used by the intent-compiler paper: a snapshot may also
carry a per-node ``price`` (tier price), per-flow ``volume_mb`` and a ``constraints`` block
(``egress_budget_mb``, ``energy_cap_w``). The weighted objective then accepts a ``cost``
component (price x CPU demand), and both backends enforce the hard constraints.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import inf
from typing import Any, Callable, Dict, Mapping, Optional

from continuum_bench._eclypse import ensure_eclypse_on_path
from continuum_bench.infra.generators import _continuum_infrastructure_class


ObjectiveSpec = str | Mapping[str, Any] | Callable[[Mapping[str, Any], Mapping[str, str]], float]


@dataclass(frozen=True)
class OracleResult:
    """Result surface for an offline reduced-snapshot oracle."""

    placement: dict[str, str]
    objective_value: float
    solver_status: str
    optimality_gap: float
    method: str


@dataclass(frozen=True)
class _SnapshotModel:
    infrastructure: Any
    application: Any


class _FixedPlacementStrategy:
    """Eclypse-compatible strategy that returns a fixed mapping."""

    def __init__(self, mapping: Dict[str, str]) -> None:
        """Store the fixed mapping returned to Eclypse."""

        self._mapping = dict(mapping)

    def place(self, infrastructure: Any, application: Any, placements: Any, placement_view: Any) -> Dict[str, str]:
        """Return the precomputed mapping for the active snapshot."""

        del infrastructure, application, placements, placement_view
        return dict(self._mapping)


def solve_small_instance(
    snapshot: Mapping[str, Any],
    objective: ObjectiveSpec = "latency",
    *,
    backend: str = "auto",
) -> OracleResult:
    """Solve a reduced placement snapshot with Eclypse-backed feasibility checks."""

    ensure_eclypse_on_path()

    services = snapshot.get("services", {})
    nodes = snapshot.get("nodes", {})
    if not services or not nodes:
        raise ValueError("snapshot must define non-empty 'services' and 'nodes'")

    model = _build_snapshot_model(snapshot)
    backend_name = str(backend).lower()
    if backend_name not in {"auto", "milp", "enumeration", "exact_enumeration"}:
        raise ValueError("backend must be one of: auto, milp, enumeration")
    if backend_name in {"auto", "milp"} and not callable(objective):
        try:
            return _solve_with_milp(model, snapshot, objective)
        except ModuleNotFoundError:
            if backend_name == "milp":
                raise
        except RuntimeError:
            if backend_name == "milp":
                raise
    if backend_name == "milp" and callable(objective):
        raise ValueError("MILP backend does not support callable objectives")
    return _solve_with_enumeration(model, snapshot, objective)


def _solve_with_enumeration(
    model: _SnapshotModel,
    snapshot: Mapping[str, Any],
    objective: ObjectiveSpec,
) -> OracleResult:
    """Solve the reduced snapshot by deterministic exhaustive enumeration."""

    services = snapshot["services"]
    nodes = snapshot["nodes"]
    service_ids = list(services)
    candidates_per_service = [_allowed_nodes(services[service_id], nodes) for service_id in service_ids]

    best_result: OracleResult | None = None
    for assignment in product(*candidates_per_service):
        placement = dict(zip(service_ids, assignment, strict=True))
        if not _satisfies_constraints(snapshot, placement):
            continue
        if not _is_feasible(model, snapshot, placement):
            continue

        objective_value = float(_evaluate_objective(model, snapshot, placement, objective))
        candidate = OracleResult(
            placement=placement,
            objective_value=objective_value,
            solver_status="optimal",
            optimality_gap=0.0,
            method="exact_enumeration",
        )
        if best_result is None or _is_better(candidate, best_result):
            best_result = candidate

    if best_result is not None:
        return best_result

    return OracleResult(
        placement={},
        objective_value=inf,
        solver_status="infeasible",
        optimality_gap=0.0,
        method="exact_enumeration",
    )


def _solve_with_milp(
    model: _SnapshotModel,
    snapshot: Mapping[str, Any],
    objective: ObjectiveSpec,
) -> OracleResult:
    """Solve the reduced snapshot with a scipy/HiGHS MILP backend."""

    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "The MILP oracle backend requires scipy. Install the optional MILP "
            "dependency, for example `pip install scipy`, or use backend='enumeration'."
        ) from exc

    weights = _milp_objective_weights(objective)
    unsupported = [
        name
        for name in ("failure_risk", "resource_overcommit")
        if float(weights.get(name, 0.0)) != 0.0
    ]
    if unsupported:
        raise RuntimeError(
            "MILP backend supports latency, energy and cost objectives; "
            f"{unsupported[0]} uses enumeration"
        )
    services = snapshot["services"]
    nodes = snapshot["nodes"]
    service_ids = list(services)
    node_ids = list(nodes)
    candidates = {
        service_id: _candidate_nodes_for_milp(services[service_id], nodes)
        for service_id in service_ids
    }
    if any(not service_candidates for service_candidates in candidates.values()):
        return OracleResult({}, inf, "infeasible", 0.0, "milp_scipy_highs")

    x_index: dict[tuple[str, str], int] = {}
    variables = 0
    for service_id in service_ids:
        for node_id in candidates[service_id]:
            x_index[(service_id, node_id)] = variables
            variables += 1

    y_index: dict[tuple[int, str, str], int] = {}
    flow_pairs: dict[int, list[tuple[str, str]]] = {}
    for flow_idx, flow in enumerate(snapshot.get("flows", [])):
        source = str(flow["source"])
        target = str(flow["target"])
        pairs: list[tuple[str, str]] = []
        for src_node in candidates[source]:
            for dst_node in candidates[target]:
                latency_value = _path_latency(model, src_node, dst_node)
                if latency_value is None:
                    continue
                y_index[(flow_idx, src_node, dst_node)] = variables
                variables += 1
                pairs.append((src_node, dst_node))
        if not pairs:
            return OracleResult({}, inf, "infeasible", 0.0, "milp_scipy_highs")
        flow_pairs[flow_idx] = pairs

    c = np.zeros(variables, dtype=float)
    _fill_milp_objective(c, x_index, y_index, model, snapshot, weights)
    integrality = np.ones(variables, dtype=int)
    bounds = Bounds(np.zeros(variables, dtype=float), np.ones(variables, dtype=float))
    no_good_assignments: list[dict[str, str]] = []

    max_solves = max(1, min(1000, _assignment_space_size(candidates)))
    for _attempt in range(max_solves):
        constraints = _milp_constraints(
            variables=variables,
            service_ids=service_ids,
            node_ids=node_ids,
            candidates=candidates,
            x_index=x_index,
            y_index=y_index,
            flow_pairs=flow_pairs,
            snapshot=snapshot,
            no_good_assignments=no_good_assignments,
            np_module=np,
            LinearConstraint=LinearConstraint,
        )
        result = milp(c=c, integrality=integrality, bounds=bounds, constraints=constraints)
        if not bool(result.success) or result.x is None:
            return OracleResult({}, inf, _milp_status(result), _milp_gap(result), "milp_scipy_highs")

        placement = _placement_from_milp_solution(result.x, service_ids, candidates, x_index)
        if _satisfies_constraints(snapshot, placement) and _is_feasible(model, snapshot, placement):
            return OracleResult(
                placement=placement,
                objective_value=float(_evaluate_objective(model, snapshot, placement, objective)),
                solver_status=_milp_status(result),
                optimality_gap=_milp_gap(result),
                method="milp_scipy_highs",
            )
        no_good_assignments.append(placement)

    return OracleResult({}, inf, "infeasible", 0.0, "milp_scipy_highs")


def _build_snapshot_model(snapshot: Mapping[str, Any]) -> _SnapshotModel:
    """Build Eclypse graphs for a reduced snapshot."""

    ensure_eclypse_on_path()
    try:
        from eclypse.graph import Application
        from eclypse.graph.assets import Symbolic
        from eclypse.graph.assets.defaults import (
            availability,
            bandwidth,
            cpu,
            get_default_path_aggregators,
            latency,
            ram,
            storage,
        )
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Eclypse is required for ContinuumBench oracle graphs. "
            "Install eclypse or set ECLYPSE_HOME to a local checkout."
        ) from exc

    SnapshotInfrastructure = _continuum_infrastructure_class()
    infrastructure = SnapshotInfrastructure(
        infrastructure_id="oracle_infra",
        include_default_assets=False,
        node_assets={
            "cpu": cpu(init_fn_or_value=0.0),
            "ram": ram(init_fn_or_value=0.0),
            "storage": storage(init_fn_or_value=0.0),
            "availability": availability(init_fn_or_value=1.0),
            "placement_layers": Symbolic(
                lower_bound=[],
                upper_bound=["space", "iot", "edge", "cloud"],
                init_fn_or_value=[],
            ),
        },
        edge_assets={
            "latency": latency(init_fn_or_value=0.0),
            "bandwidth": bandwidth(init_fn_or_value=0.0),
        },
        path_assets_aggregators=get_default_path_aggregators(),
    )

    nodes = snapshot["nodes"]
    services = snapshot["services"]
    for node_id, attrs in nodes.items():
        node_attrs = dict(attrs)
        node_attrs.setdefault("placement_layers", _node_layers(node_attrs))
        infrastructure.add_node(
            node_id,
            strict=False,
            cpu=float(node_attrs.get("capacity", node_attrs.get("cpu", 0.0))),
            ram=float(node_attrs.get("ram", node_attrs.get("capacity", 0.0))),
            storage=float(node_attrs.get("storage", node_attrs.get("capacity", 0.0))),
            availability=float(node_attrs.get("availability", 1.0)),
            placement_layers=list(node_attrs.get("placement_layers", [])),
        )

    for link_key, attrs in snapshot.get("links", {}).items():
        src, dst = link_key
        link_attrs = {"latency": attrs} if isinstance(attrs, (int, float)) else dict(attrs)
        infrastructure.add_edge(
            src,
            dst,
            strict=False,
            latency=float(link_attrs.get("latency", 0.0)),
            bandwidth=float(link_attrs.get("bandwidth", 0.0)),
        )

    application = Application(
        application_id="oracle_app",
        include_default_assets=False,
        node_assets={
            "cpu": cpu(init_fn_or_value=0.0),
            "ram": ram(init_fn_or_value=0.0),
            "storage": storage(init_fn_or_value=0.0),
            "placement_layers": Symbolic(
                lower_bound=[],
                upper_bound=["space", "iot", "edge", "cloud"],
                init_fn_or_value=[],
            ),
        },
    )

    for service_id, attrs in services.items():
        service_attrs = dict(attrs)
        service_layers = _service_layers(service_attrs)
        if service_layers is not None:
            service_attrs["placement_layers"] = service_layers
        application.add_node(
            service_id,
            strict=False,
            cpu=float(service_attrs.get("demand", service_attrs.get("cpu", 0.0))),
            ram=float(service_attrs.get("ram", 0.0)),
            storage=float(service_attrs.get("storage", 0.0)),
            placement_layers=list(service_attrs.get("placement_layers", [])),
        )

    for flow in snapshot.get("flows", []):
        application.add_edge(
            str(flow["source"]),
            str(flow["target"]),
            strict=False,
            weight=float(flow.get("weight", 1.0)),
        )

    return _SnapshotModel(infrastructure=infrastructure, application=application)


def _milp_objective_weights(objective: ObjectiveSpec) -> dict[str, float]:
    """Return linear objective weights supported by the MILP backend."""

    if callable(objective):
        raise ValueError("MILP backend does not support callable objectives")
    if isinstance(objective, Mapping):
        weights = objective.get("weights", {})
        if not isinstance(weights, Mapping) or not weights:
            raise ValueError("weighted objective must define a non-empty 'weights' mapping")
        supported = {"latency", "energy", "cost", "resource_overcommit", "failure_risk"}
        unknown = set(str(name) for name in weights) - supported
        if unknown:
            raise ValueError(f"unsupported weighted objective component: {sorted(unknown)[0]}")
        return {str(name): float(weight) for name, weight in weights.items()}
    if objective != "latency":
        raise ValueError(f"unsupported objective: {objective}")
    return {"latency": 1.0}


def _candidate_nodes_for_milp(service: Mapping[str, Any], nodes: Mapping[str, Any]) -> list[str]:
    """Return allowed candidate nodes after static layer filtering."""

    allowed = _allowed_nodes(service, nodes)
    service_layers = _service_layers(service)
    if not service_layers:
        return allowed
    return [
        node_id
        for node_id in allowed
        if set(service_layers).issubset(set(_node_layers(nodes[node_id])))
    ]


def _path_latency(model: _SnapshotModel, src_node: str, dst_node: str) -> Optional[float]:
    """Return finite path latency for a candidate node pair."""

    path = model.infrastructure.path(src_node, dst_node)
    if path is None:
        return None
    latency_value = float(model.infrastructure.path_resources(src_node, dst_node).get("latency", inf))
    if latency_value == inf:
        return None
    return latency_value


def _fill_milp_objective(
    c: Any,
    x_index: Mapping[tuple[str, str], int],
    y_index: Mapping[tuple[int, str, str], int],
    model: _SnapshotModel,
    snapshot: Mapping[str, Any],
    weights: Mapping[str, float],
) -> None:
    """Populate the linear MILP objective coefficients."""

    latency_weight = float(weights.get("latency", 0.0))
    energy_weight = float(weights.get("energy", 0.0))
    cost_weight = float(weights.get("cost", 0.0))

    for (service_id, node_id), idx in x_index.items():
        service = snapshot["services"][service_id]
        node = snapshot["nodes"][node_id]
        if latency_weight:
            c[idx] += latency_weight * float(service.get("node_costs", {}).get(node_id, 0.0))
        if energy_weight:
            c[idx] += energy_weight * _assignment_energy(service, node)
        if cost_weight:
            c[idx] += cost_weight * _assignment_cost(service, node)
    if latency_weight:
        for flow_idx, flow in enumerate(snapshot.get("flows", [])):
            flow_weight = float(flow.get("weight", 1.0))
            for key, idx in y_index.items():
                if key[0] != flow_idx:
                    continue
                _, src_node, dst_node = key
                c[idx] += latency_weight * flow_weight * float(
                    _path_latency(model, src_node, dst_node) or 0.0
                )


def _assignment_energy(service: Mapping[str, Any], node: Mapping[str, Any]) -> float:
    """Return the linear (dynamic-power) energy contribution for one service-node assignment.

    Idle power is excluded for the reason given in ``_energy_objective``: it does not depend on
    the placement, and adding it per assignment made every finite energy cap infeasible.
    """

    demand = float(service.get("demand", service.get("cpu", 0.0)))
    total = float(node.get("dynamic_power_w_per_cpu", 0.0)) * demand
    total += float(node.get("dynamic_power_w_per_ram", 0.0)) * float(service.get("ram", 0.0))
    total += float(node.get("dynamic_power_w_per_storage", 0.0)) * float(service.get("storage", 0.0))
    return total


def _assignment_cost(service: Mapping[str, Any], node: Mapping[str, Any]) -> float:
    """Return the tier-priced provisioning cost of one service-node assignment.

    ``cost_scale`` (snapshot-level, default 1.0) expresses one priced CPU unit in the latency
    objective's units (milliseconds); the theta projection sets it so that w_cost = 1 weighs a
    tier-price difference comparably to a WAN-hop latency difference.
    """

    demand = float(service.get("demand", service.get("cpu", 0.0)))
    return float(node.get("price", 1.0)) * demand * float(node.get("cost_scale", 1.0))


def _node_tier(node: Mapping[str, Any]) -> str:
    """Return the node's single tier label, or '' when it is not tier-anchored."""

    layers = _node_layers(node)
    return layers[0] if len(layers) == 1 else ""


def _is_egress_flow(nodes: Mapping[str, Any], src_node: str, dst_node: str) -> bool:
    """Return whether a flow between two nodes crosses into the cloud tier."""

    return _node_tier(nodes[dst_node]) == "cloud" and _node_tier(nodes[src_node]) != "cloud"


def _satisfies_constraints(snapshot: Mapping[str, Any], placement: Mapping[str, str]) -> bool:
    """Check the optional hard constraints (egress budget, energy cap) for a placement."""

    constraints = snapshot.get("constraints", {}) or {}
    nodes = snapshot["nodes"]
    egress_budget = float(constraints.get("egress_budget_mb", inf))
    if egress_budget != inf:
        egress = sum(
            float(flow.get("volume_mb", 0.0))
            for flow in snapshot.get("flows", [])
            if _is_egress_flow(nodes, placement[flow["source"]], placement[flow["target"]])
        )
        if egress > egress_budget + 1e-9:
            return False
    energy_cap = float(constraints.get("energy_cap_w", inf))
    if energy_cap != inf and _energy_objective(snapshot, placement) > energy_cap + 1e-9:
        return False
    return True


def _milp_constraints(
    *,
    variables: int,
    service_ids: list[str],
    node_ids: list[str],
    candidates: Mapping[str, list[str]],
    x_index: Mapping[tuple[str, str], int],
    y_index: Mapping[tuple[int, str, str], int],
    flow_pairs: Mapping[int, list[tuple[str, str]]],
    snapshot: Mapping[str, Any],
    no_good_assignments: list[dict[str, str]],
    np_module: Any,
    LinearConstraint: Any,
) -> list[Any]:
    """Build scipy linear constraints for the reduced placement MILP."""

    constraints: list[Any] = []

    for service_id in service_ids:
        row = np_module.zeros(variables, dtype=float)
        for node_id in candidates[service_id]:
            row[x_index[(service_id, node_id)]] = 1.0
        constraints.append(LinearConstraint(row, 1.0, 1.0))

    for node_id in node_ids:
        row = np_module.zeros(variables, dtype=float)
        for service_id in service_ids:
            if node_id in candidates[service_id]:
                demand = float(snapshot["services"][service_id].get("demand", snapshot["services"][service_id].get("cpu", 0.0)))
                row[x_index[(service_id, node_id)]] = demand
        capacity = float(snapshot["nodes"][node_id].get("capacity", snapshot["nodes"][node_id].get("cpu", 0.0)))
        constraints.append(LinearConstraint(row, -np_module.inf, capacity))

    for flow_idx, flow in enumerate(snapshot.get("flows", [])):
        source = str(flow["source"])
        target = str(flow["target"])
        pairs = flow_pairs[flow_idx]
        for src_node in candidates[source]:
            row = np_module.zeros(variables, dtype=float)
            for dst_node in candidates[target]:
                idx = y_index.get((flow_idx, src_node, dst_node))
                if idx is not None:
                    row[idx] = 1.0
            row[x_index[(source, src_node)]] = -1.0
            constraints.append(LinearConstraint(row, 0.0, 0.0))
        for dst_node in candidates[target]:
            row = np_module.zeros(variables, dtype=float)
            for src_node in candidates[source]:
                idx = y_index.get((flow_idx, src_node, dst_node))
                if idx is not None:
                    row[idx] = 1.0
            row[x_index[(target, dst_node)]] = -1.0
            constraints.append(LinearConstraint(row, 0.0, 0.0))
        row = np_module.zeros(variables, dtype=float)
        for src_node, dst_node in pairs:
            row[y_index[(flow_idx, src_node, dst_node)]] = 1.0
        constraints.append(LinearConstraint(row, 1.0, 1.0))

    hard = snapshot.get("constraints", {}) or {}
    egress_budget = float(hard.get("egress_budget_mb", inf))
    if egress_budget != inf:
        row = np_module.zeros(variables, dtype=float)
        for flow_idx, flow in enumerate(snapshot.get("flows", [])):
            volume = float(flow.get("volume_mb", 0.0))
            for src_node, dst_node in flow_pairs[flow_idx]:
                if _is_egress_flow(snapshot["nodes"], src_node, dst_node):
                    row[y_index[(flow_idx, src_node, dst_node)]] += volume
        constraints.append(LinearConstraint(row, -np_module.inf, egress_budget))
    energy_cap = float(hard.get("energy_cap_w", inf))
    if energy_cap != inf:
        row = np_module.zeros(variables, dtype=float)
        for (service_id, node_id), idx in x_index.items():
            row[idx] = _assignment_energy(snapshot["services"][service_id], snapshot["nodes"][node_id])
        constraints.append(LinearConstraint(row, -np_module.inf, energy_cap))

    for placement in no_good_assignments:
        row = np_module.zeros(variables, dtype=float)
        for service_id, node_id in placement.items():
            idx = x_index.get((service_id, node_id))
            if idx is not None:
                row[idx] = 1.0
        constraints.append(LinearConstraint(row, -np_module.inf, len(service_ids) - 1))

    return constraints


def _placement_from_milp_solution(
    solution: Any,
    service_ids: list[str],
    candidates: Mapping[str, list[str]],
    x_index: Mapping[tuple[str, str], int],
) -> dict[str, str]:
    """Decode one-hot service-node assignment variables."""

    placement: dict[str, str] = {}
    for service_id in service_ids:
        selected = max(
            candidates[service_id],
            key=lambda node_id: float(solution[x_index[(service_id, node_id)]]),
        )
        placement[service_id] = selected
    return placement


def _assignment_space_size(candidates: Mapping[str, list[str]]) -> int:
    """Return the product of candidate counts for no-good iteration limits."""

    total = 1
    for service_candidates in candidates.values():
        total *= max(1, len(service_candidates))
    return total


def _milp_status(result: Any) -> str:
    """Normalize scipy MILP solver status."""

    if bool(getattr(result, "success", False)):
        return "optimal"
    return str(getattr(result, "message", "infeasible")).lower().replace(" ", "_")


def _milp_gap(result: Any) -> float:
    """Return a conservative optimality gap when exposed by the solver."""

    mip_gap = getattr(result, "mip_gap", None)
    if mip_gap is None:
        return 0.0 if bool(getattr(result, "success", False)) else inf
    return float(mip_gap)


def _is_feasible(model: _SnapshotModel, snapshot: Mapping[str, Any], placement: Mapping[str, str]) -> bool:
    """Validate a placement through Eclypse's placement lifecycle."""

    from eclypse.placement import Placement, PlacementManager

    for service_id, node_id in placement.items():
        if node_id not in snapshot["services"][service_id].get("allowed_nodes", snapshot["nodes"].keys()):
            return False

    manager = PlacementManager(infrastructure=model.infrastructure)
    fixed = Placement(
        infrastructure=model.infrastructure,
        application=model.application,
        strategy=_FixedPlacementStrategy(dict(placement)),
    )
    manager.placements[model.application.id] = fixed
    violations = list(manager.mapping_phase())
    if not fixed.mapping:
        return False
    if fixed.is_partial:
        return False
    return not any(nodes for _placement, nodes in violations)


def _evaluate_objective(
    model: _SnapshotModel,
    snapshot: Mapping[str, Any],
    placement: Mapping[str, str],
    objective: ObjectiveSpec,
) -> float:
    """Evaluate the configured objective for a placement."""

    if callable(objective):
        return float(objective(snapshot, placement))

    if isinstance(objective, Mapping):
        return _weighted_objective(model, snapshot, placement, objective)

    if objective != "latency":
        raise ValueError(f"unsupported objective: {objective}")

    return _latency_objective(model, snapshot, placement)


def _weighted_objective(
    model: _SnapshotModel,
    snapshot: Mapping[str, Any],
    placement: Mapping[str, str],
    objective: Mapping[str, Any],
) -> float:
    """Compute a configurable multi-objective oracle score."""

    weights = objective.get("weights", {})
    if not isinstance(weights, Mapping) or not weights:
        raise ValueError("weighted objective must define a non-empty 'weights' mapping")

    components = {
        "latency": _latency_objective(model, snapshot, placement),
        "energy": _energy_objective(snapshot, placement),
        "cost": _cost_objective(snapshot, placement),
        "resource_overcommit": _resource_overcommit_objective(snapshot, placement),
        "failure_risk": _failure_risk_objective(model, snapshot, placement),
    }

    total = 0.0
    for name, raw_weight in weights.items():
        if name not in components:
            raise ValueError(f"unsupported weighted objective component: {name}")
        total += float(raw_weight) * components[name]
    return total


def _latency_objective(model: _SnapshotModel, snapshot: Mapping[str, Any], placement: Mapping[str, str]) -> float:
    """Compute the latency objective for a placement."""

    total = 0.0
    services = snapshot["services"]
    for service_id, node_id in placement.items():
        total += float(services[service_id].get("node_costs", {}).get(node_id, 0.0))

    for flow in snapshot.get("flows", []):
        src_node = placement[flow["source"]]
        dst_node = placement[flow["target"]]
        path = model.infrastructure.path(src_node, dst_node)
        if path is None:
            return inf
        total += float(flow.get("weight", 1.0)) * float(
            model.infrastructure.path_resources(src_node, dst_node).get("latency", inf)
        )
    return total


def _energy_objective(snapshot: Mapping[str, Any], placement: Mapping[str, str]) -> float:
    """Estimate the placement's dynamic power (W) from node power coefficients.

    Idle power is excluded: the simulator keeps every node powered whatever the placement, so
    idle power is a constant of the infrastructure, not of the decision.
    """

    total = 0.0
    for service_id, node_id in placement.items():
        service = snapshot["services"][service_id]
        node = snapshot["nodes"][node_id]
        demand = float(service.get("demand", service.get("cpu", 0.0)))
        total += float(node.get("dynamic_power_w_per_cpu", 0.0)) * demand
        total += float(node.get("dynamic_power_w_per_ram", 0.0)) * float(service.get("ram", 0.0))
        total += float(node.get("dynamic_power_w_per_storage", 0.0)) * float(service.get("storage", 0.0))
    return total


def _cost_objective(snapshot: Mapping[str, Any], placement: Mapping[str, str]) -> float:
    """Sum tier-priced provisioning cost over placed services."""

    return sum(
        _assignment_cost(snapshot["services"][service_id], snapshot["nodes"][node_id])
        for service_id, node_id in placement.items()
    )


def _resource_overcommit_objective(snapshot: Mapping[str, Any], placement: Mapping[str, str]) -> float:
    """Return total demand above node capacity."""

    used_capacity = {node_id: 0.0 for node_id in snapshot["nodes"]}
    for service_id, node_id in placement.items():
        service = snapshot["services"][service_id]
        used_capacity[node_id] += float(service.get("demand", service.get("cpu", 0.0)))

    return sum(
        max(0.0, used_capacity[node_id] - float(node.get("capacity", node.get("cpu", 0.0))))
        for node_id, node in snapshot["nodes"].items()
    )


def _failure_risk_objective(
    model: _SnapshotModel,
    snapshot: Mapping[str, Any],
    placement: Mapping[str, str],
) -> float:
    """Estimate placement failure risk from node availability metadata."""

    success_probability = 1.0
    for node_id in set(placement.values()):
        success_probability *= max(0.0, min(1.0, float(snapshot["nodes"][node_id].get("availability", 1.0))))

    for flow in snapshot.get("flows", []):
        src_node = placement[flow["source"]]
        dst_node = placement[flow["target"]]
        if model.infrastructure.path(src_node, dst_node) is None:
            return 1.0
    return 1.0 - success_probability


def _allowed_nodes(service: Mapping[str, Any], nodes: Mapping[str, Any]) -> list[str]:
    """Return the allowed node IDs for a service."""

    allowed = list(service.get("allowed_nodes", nodes.keys()))
    if not allowed:
        raise ValueError("each service must allow at least one node")
    for node_id in allowed:
        if node_id not in nodes:
            raise ValueError(f"unknown node in allowed_nodes: {node_id}")
    return allowed


def _service_layers(service: Mapping[str, Any]) -> Optional[list[str]]:
    """Return the symbolic placement layers for a service if present."""

    layers = service.get("placement_layers")
    if isinstance(layers, list) and layers:
        return [str(layer).lower() for layer in layers]
    return None


def _node_layers(node: Mapping[str, Any]) -> list[str]:
    """Return explicit symbolic placement layers for a node."""

    layers = node.get("placement_layers")
    if isinstance(layers, list) and layers:
        return [str(layer).lower() for layer in layers]
    return []


def _is_better(candidate: OracleResult, incumbent: OracleResult) -> bool:
    """Return whether a candidate result improves on the incumbent."""

    if candidate.objective_value != incumbent.objective_value:
        return candidate.objective_value < incumbent.objective_value
    return tuple(sorted(candidate.placement.items())) < tuple(sorted(incumbent.placement.items()))
