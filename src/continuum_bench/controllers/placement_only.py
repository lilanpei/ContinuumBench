from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Optional

from continuum_bench.controllers.interfaces import ControllerAction, ControllerState
from continuum_bench.controllers.placement_views import mapping_fits_node_capacity, plan_placement
from continuum_bench.oracles import solve_small_instance
from continuum_bench.placement import MinCTStrategy
from continuum_bench.placement.strategies import (
    BestFitStrategy,
    CostGreedyStrategy,
    DependencyAwareStrategy,
    NearestFeasibleEdgeStrategy,
    RoundRobinStrategy as ContinuumRoundRobinStrategy,
)
from continuum_bench.placement.strategies import (
    _candidate_nodes_for_service,
    _deployment_cost_score,
    _graph_node_attrs,
    _node_matches_layer,
    _stage_output_volume_mb,
)


_EXTREME_TIER_LAYERS = {
    "all_local": "iot",
    "all_edge": "edge",
    "all_cloud": "cloud",
}


@dataclass
class PlacementOnlyController:
    """Placement-only baseline controller (no autoscaling)."""

    strategy: str = "round_robin"
    replan_every_epochs: int = 1
    static_mapping: Optional[Dict[str, str]] = None
    freeze_after_initial: bool = False

    milp_fallbacks: int = 0

    def __post_init__(self):
        """Normalize the strategy key and instantiate the strategy."""
        self._strategy_key = self.strategy.lower().replace("-", "_")
        self._strategy = self._build_strategy(self.strategy)
        self._last_placement: Dict[str, str] = {}

    def step(self, state: ControllerState) -> ControllerAction:
        """Return a placement decision for the current epoch."""
        active_services = self._active_services(state)
        active_service_set = set(active_services)

        if self._last_placement and (
            self.freeze_after_initial
            or state.epoch % max(1, self.replan_every_epochs) != 0
        ):
            return ControllerAction(
                placement=None,
                metadata={
                    "controller": "placement_only",
                    "strategy": self.strategy,
                    "replan": False,
                    "replan_deferred": False,
                },
            )

        if self._strategy_key == "static":
            placement = {
                service_id: node_id
                for service_id, node_id in (self.static_mapping or {}).items()
                if service_id in active_service_set
            }
        elif self._strategy_key in _EXTREME_TIER_LAYERS:
            placement = self._extreme_tier_placement(
                state,
                active_services,
                target_layer=_EXTREME_TIER_LAYERS[self._strategy_key],
            )
        elif self._strategy_key in {"milp", "solver_milp"}:
            placement = self._milp_placement(state, active_services)
        else:
            placement = plan_placement(state, self._strategy, active_services)

        if not placement:
            if self._last_placement and state.placement:
                return ControllerAction(
                    placement=None,
                    metadata={
                        "controller": "placement_only",
                        "strategy": self.strategy,
                        "replan": False,
                        "replan_deferred": True,
                    },
                )
            raise ValueError(
                f"placement-only strategy '{self.strategy}' could not place active services"
            )

        self._last_placement = dict(placement)
        return ControllerAction(
            placement=dict(placement),
            metadata={
                "controller": "placement_only",
                "strategy": self.strategy,
                "replan": True,
                "replan_deferred": False,
            },
        )

    def _build_strategy(self, strategy: str):
        """Create the configured placement strategy instance."""
        strategy_lower = strategy.lower().replace("-", "_")
        if strategy_lower in {"round_robin", "rr"}:
            return ContinuumRoundRobinStrategy()
        if strategy_lower in {"best_fit", "bestfit"}:
            return BestFitStrategy()
        if strategy_lower in {"min_latency", "min_ct", "heft_like"}:
            return MinCTStrategy()
        if strategy_lower == "nearest_feasible_edge":
            return NearestFeasibleEdgeStrategy()
        if strategy_lower == "cost_greedy":
            return CostGreedyStrategy()
        if strategy_lower in {"dependency_aware", "critical_path"}:
            return DependencyAwareStrategy()
        if strategy_lower in {"milp", "solver_milp"}:
            return None
        if strategy_lower == "static":
            return None
        if strategy_lower in _EXTREME_TIER_LAYERS:
            return None
        raise ValueError(
            "placement-only strategy must be one of: static, round_robin, best_fit, "
            "min_latency, nearest_feasible_edge, cost_greedy, dependency_aware, critical_path, "
            "milp, all_local, all_edge, all_cloud"
        )

    def _active_services(self, state: ControllerState) -> List[str]:
        """Return services that should participate in placement."""
        worker_ids = set()
        active_worker_ids = set()
        for snapshot in state.worker_pools.values():
            active_worker_ids.update(snapshot.active_worker_ids)
            worker_ids.update(snapshot.active_worker_ids)
            worker_ids.update(snapshot.inactive_worker_ids)

        if not worker_ids:
            return list(state.metadata.get("active_services", list(state.application.nodes)))

        return [
            service_id
            for service_id in state.application.nodes
            if service_id not in worker_ids or service_id in active_worker_ids
        ]

    def _extreme_tier_placement(
        self,
        state: ControllerState,
        active_services: List[str],
        *,
        target_layer: str,
    ) -> Dict[str, str]:
        """Place services on the requested layer when candidates exist."""
        placement: Dict[str, str] = {}
        for idx, service_id in enumerate(active_services):
            candidates = _candidate_nodes_for_service(
                state.infrastructure,
                state.application,
                service_id,
            )
            if not candidates:
                continue

            tier_candidates = [
                node_id
                for node_id in candidates
                if _node_matches_layer(
                    node_id,
                    target_layer,
                    node_attrs=_graph_node_attrs(state.infrastructure, node_id),
                )
            ]
            selected_candidates = tier_candidates or candidates
            placement[service_id] = selected_candidates[idx % len(selected_candidates)]

        return placement

    def _milp_placement(
        self,
        state: ControllerState,
        active_services: List[str],
    ) -> Dict[str, str]:
        """Place active services with the offline solver-backed reduced oracle.

        The reduced model's demand figures are approximate; a mapping that over-subscribes a
        node at runtime is replaced by the min-latency heuristic's (the autoscaler path defers
        instead, which a placement-only controller cannot do at t = 0). Fallbacks are counted in
        ``milp_fallbacks``.
        """

        mapping = milp_plan_placement(state, active_services)
        if mapping and not mapping_fits_node_capacity(state, mapping):
            self.milp_fallbacks += 1
            return plan_placement(state, MinCTStrategy(), active_services)
        return mapping


def milp_plan_placement(
    state: ControllerState,
    active_services: List[str],
) -> Dict[str, str]:
    """Solve a global MILP placement (latency objective) for the active services.

    Returns a full mapping that may differ from any prior placement---i.e. it may migrate existing
    services to reach the global optimum---or ``{}`` when the solver cannot place them. Shared by the
    placement-only controller and the autoscaling controllers' MILP replan strategy, so that an
    autoscaler can use a genuinely re-optimizing (migrating) planner rather than an order-stable heuristic.
    """

    spec = _milp_spec(state)
    snapshot = _build_milp_snapshot(state, active_services, spec=spec)
    objective = {"weights": dict(spec["weights"])} if spec.get("weights") else "latency"
    try:
        result = solve_small_instance(snapshot, objective=objective, backend="milp")
    except ValueError as exc:
        if "allow at least one node" not in str(exc):
            raise
        return {}
    if result.solver_status != "optimal" or not result.placement:
        return {}
    return dict(result.placement)


# Tiers that host unanchored compute stages ("" = a node without a tier label).
_COMPUTE_TIERS = {"edge", "cloud", ""}

_RESIDENCY_FILTERS = {
    "any": lambda layer: True,
    "edge_only": lambda layer: layer == "edge",
    "cloud_only": lambda layer: layer == "cloud",
    "no_cloud": lambda layer: layer != "cloud",
}


def _milp_spec(state: ControllerState) -> Dict:
    """Return the MILP placement policy (``placement.milp``, set through theta) from the state.

    When it is absent, the oracle runs its pure-latency objective with no tier prices,
    residency filter or hard caps.
    """

    spec = dict((state.metadata or {}).get("milp_objective") or {})
    spec.setdefault("weights", {})
    spec.setdefault("tier_prices", {})
    spec.setdefault("residency", "any")
    if str(spec["residency"]) not in _RESIDENCY_FILTERS:
        raise ValueError(f"unknown residency policy: {spec['residency']!r}")
    return spec


def _build_milp_snapshot(
    state: ControllerState,
    active_services: List[str],
    spec: Optional[Dict] = None,
) -> Dict[str, Dict]:
    """Build a reduced static placement snapshot for the MILP oracle."""

    spec = spec if spec is not None else _milp_spec(state)
    active_set = set(active_services)
    residual_nodes = None
    placement_view = state.metadata.get("placement_view") if state.metadata else None
    if placement_view is not None and hasattr(placement_view, "residual"):
        residual = getattr(placement_view, "residual", None)
        if residual is not None and hasattr(residual, "nodes"):
            residual_nodes = {str(node_id): dict(attrs) for node_id, attrs in residual.nodes(data=True)}

    services = {
        service_id: _milp_service_attrs(state, service_id, residual_nodes=residual_nodes)
        for service_id in active_services
        if service_id in state.application.nodes
    }
    nodes = {
        str(node_id): _milp_node_attrs(
            attrs, tier_prices=spec.get("tier_prices", {}), cost_scale=float(spec.get("cost_scale", 1.0))
        )
        for node_id, attrs in _graph_nodes_with_attrs(state.infrastructure)
    }
    # Residency is a hard constraint on stages the scenario has not already anchored to a
    # tier (sources and other layer-pinned stages keep their anchoring).
    # Unanchored stages are placed on the continuum's compute tiers only. The source tiers
    # (iot, space) host their anchored sources and carry a fixed price, so without this filter
    # a cost-weighted MILP would move compute stages onto a 4-CPU device node.
    residency_ok = _RESIDENCY_FILTERS[str(spec.get("residency", "any"))]
    for service in services.values():
        if service["placement_layers"]:
            continue
        service["allowed_nodes"] = [
            node_id for node_id in service["allowed_nodes"]
            if _single_layer(nodes[node_id]["placement_layers"]) in _COMPUTE_TIERS
            and residency_ok(_single_layer(nodes[node_id]["placement_layers"]))
        ]
    links = {
        (str(src), str(dst)): _milp_link_attrs(attrs)
        for src, dst, attrs in _graph_edges_with_attrs(state.infrastructure)
    }
    flows = [
        {
            "source": str(src),
            "target": str(dst),
            "weight": float(attrs.get("weight", 1.0)) if isinstance(attrs, dict) else 1.0,
            "volume_mb": _stage_output_volume_mb(state.application, str(src)),
        }
        for src, dst, attrs in _graph_edges_with_attrs(state.application)
        if src in active_set and dst in active_set
    ]
    snapshot = {
        "services": services,
        "nodes": nodes,
        "links": links,
        "flows": flows,
    }
    constraints = {
        key: float(spec[key])
        for key in ("egress_budget_mb", "energy_cap_w")
        if key in spec and spec[key] is not None
    }
    if constraints:
        snapshot["constraints"] = constraints
    return snapshot


def _single_layer(layers: List[str]) -> str:
    """Return a node's single tier label, or '' when it is not tier-anchored."""

    return str(layers[0]).lower() if len(layers) == 1 else ""


def _milp_service_attrs(
    state: ControllerState,
    service_id: str,
    *,
    residual_nodes: Optional[Dict[str, Dict[str, float]]],
) -> Dict:
    """Return service attributes for a reduced MILP snapshot."""

    service_attrs = dict(state.application.nodes.get(service_id, {}))
    service_attrs.update(dict(state.metadata.get("service_base_attrs", {}).get(service_id, {})))
    candidates = _candidate_nodes_for_service(
        state.infrastructure,
        state.application,
        service_id,
        residual_nodes=residual_nodes,
    )
    result = {
        "demand": float(service_attrs.get("cpu", service_attrs.get("demand", 0.0))),
        "cpu": float(service_attrs.get("cpu", service_attrs.get("demand", 0.0))),
        "ram": float(service_attrs.get("ram", 0.0)),
        "storage": float(service_attrs.get("storage", 0.0)),
        "allowed_nodes": list(candidates),
        "placement_layers": list(service_attrs.get("placement_layers", [])),
        "node_costs": {
            node_id: _finite_milp_node_cost(
                _deployment_cost_score(
                    state.application,
                    state.infrastructure,
                    service_id,
                    node_id,
                    state.placement,
                )
            )
            for node_id in candidates
        },
    }
    return result


def _finite_milp_node_cost(value: float) -> float:
    """Return a finite auxiliary node cost for SciPy/HiGHS."""

    cost = float(value)
    if math.isfinite(cost):
        return cost
    return 1_000_000.0


def _milp_node_attrs(
    attrs: Dict[str, object],
    tier_prices: Optional[Dict[str, float]] = None,
    cost_scale: float = 1.0,
) -> Dict[str, object]:
    """Return node attributes understood by the reduced oracle."""

    layers = list(attrs.get("placement_layers", []))
    price = float((tier_prices or {}).get(_single_layer(layers), 1.0))
    return {
        "price": price,
        "cost_scale": float(cost_scale),
        "capacity": float(attrs.get("cpu", attrs.get("capacity", 0.0))),
        "cpu": float(attrs.get("cpu", attrs.get("capacity", 0.0))),
        "ram": float(attrs.get("ram", attrs.get("capacity", 0.0))),
        "storage": float(attrs.get("storage", attrs.get("capacity", 0.0))),
        "availability": float(attrs.get("availability", 1.0)),
        "placement_layers": list(attrs.get("placement_layers", [])),
        "idle_power_w": float(attrs.get("idle_power_w", 0.0)),
        "dynamic_power_w_per_cpu": float(attrs.get("dynamic_power_w_per_cpu", 0.0)),
        "dynamic_power_w_per_ram": float(attrs.get("dynamic_power_w_per_ram", 0.0)),
        "dynamic_power_w_per_storage": float(attrs.get("dynamic_power_w_per_storage", 0.0)),
    }


def _milp_link_attrs(attrs: Dict[str, object]) -> Dict[str, float]:
    """Return stable structural link attributes for the reduced oracle."""

    return {
        "latency": float(attrs.get("latency", attrs.get("contact_latency", 0.0))),
        "bandwidth": float(attrs.get("bandwidth", attrs.get("contact_bandwidth", 0.0))),
    }


def _graph_nodes_with_attrs(graph: object) -> List[tuple[str, Dict[str, object]]]:
    """Return graph nodes as plain dictionaries."""

    nodes = getattr(graph, "nodes", {})
    try:
        return [(str(node_id), dict(attrs)) for node_id, attrs in nodes(data=True)]
    except TypeError:
        if hasattr(nodes, "items"):
            return [(str(node_id), dict(attrs)) for node_id, attrs in nodes.items()]
        return [(str(node_id), dict(nodes[node_id])) for node_id in nodes]


def _graph_edges_with_attrs(graph: object) -> List[tuple[str, str, Dict[str, object]]]:
    """Return graph edges as plain dictionaries."""

    edges = getattr(graph, "edges", None)
    if callable(edges):
        return [(str(src), str(dst), dict(attrs)) for src, dst, attrs in edges(data=True)]
    return []
