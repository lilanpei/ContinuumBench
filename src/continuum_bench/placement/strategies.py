"""Minimal placement strategies for the benchmark package."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional

from continuum_bench._eclypse import ensure_eclypse_on_path

ensure_eclypse_on_path()

try:
    from eclypse.placement.strategies import BestFitStrategy as EclypseBestFitStrategy
    from eclypse.placement.strategies.strategy import PlacementStrategy
    from eclypse.placement.strategies.round_robin import (
        RoundRobinStrategy as EclypseRoundRobinStrategy,
    )
except ModuleNotFoundError as exc:
    raise ModuleNotFoundError(
        "Eclypse is required for ContinuumBench placement strategies. "
        "Install eclypse or set ECLYPSE_HOME to a local checkout."
    ) from exc


if TYPE_CHECKING:
    from eclypse.graph import Application, Infrastructure
    from eclypse.placement import Placement, PlacementView


def _graph_node_attrs(graph: Any, node_id: str) -> Dict[str, Any]:
    """Return node attributes as a plain dictionary."""
    nodes = getattr(graph, "nodes", {})
    if hasattr(nodes, "get"):
        return dict(nodes.get(node_id, {}))
    try:
        return dict(nodes[node_id])
    except Exception:
        return {}


def _available_nodes(infrastructure: "Infrastructure") -> List[str]:
    """Return node IDs from the available infrastructure view."""
    available = getattr(infrastructure, "available", None)
    if available is not None and hasattr(available, "nodes"):
        return list(available.nodes)
    return list(getattr(infrastructure, "nodes", []))


def _residual_node_state(
    infrastructure: "Infrastructure",
    placement_view: Optional["PlacementView"],
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Return a mutable snapshot of the residual node resources, if available."""

    if placement_view is None or not hasattr(placement_view, "residual"):
        return None

    residual = getattr(placement_view, "residual", None)
    if residual is None or not hasattr(residual, "nodes"):
        return None

    return {
        str(node_id): dict(attrs)
        for node_id, attrs in residual.nodes(data=True)
    }


def _node_layer(infrastructure: "Infrastructure", node_id: str) -> str:
    """Return the Eclypse symbolic placement layer for a node."""
    placement_layers = _graph_node_attrs(infrastructure, node_id).get("placement_layers", [])
    if isinstance(placement_layers, list) and len(placement_layers) == 1:
        inferred_layer = str(placement_layers[0]).lower()
        if inferred_layer in {"space", "cloud", "edge", "iot"}:
            return inferred_layer
    return ""


def _node_matches_layer(
    node_id: str,
    layer: str,
    node_attrs: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return whether a node declares the requested Eclypse symbolic layer."""
    del node_id
    placement_layers = (node_attrs or {}).get("placement_layers", [])
    if isinstance(placement_layers, list) and len(placement_layers) == 1:
        inferred_layer = str(placement_layers[0]).lower()
        return inferred_layer == layer
    return False


def _placement_layer_for_service(application: "Application", service_id: str) -> Optional[str]:
    """Return the Eclypse symbolic layer a service requires, if any."""
    node_data = _graph_node_attrs(application, service_id)

    placement_layers = node_data.get("placement_layers", [])
    if isinstance(placement_layers, list) and len(placement_layers) == 1:
        inferred_layer = str(placement_layers[0]).lower()
        if inferred_layer in {"space", "cloud", "edge", "iot"}:
            return inferred_layer
    return None


def _is_placement_constrained_service(application: "Application", service_id: str) -> bool:
    """Return whether a service is constrained to a specific placement layer."""
    return _placement_layer_for_service(application, service_id) is not None


def _candidate_nodes_for_service(
    infrastructure: "Infrastructure",
    application: "Application",
    service_id: str,
    residual_nodes: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Return feasible candidate nodes for a service."""
    if residual_nodes is not None:
        nodes = list(residual_nodes.keys())
    else:
        nodes = _available_nodes(infrastructure)

    placement_layer = _placement_layer_for_service(application, service_id)
    if placement_layer is None:
        candidates = nodes
    else:
        candidates = [
            node_id
            for node_id in nodes
            if _node_matches_layer(
                node_id,
                placement_layer,
                node_attrs=_graph_node_attrs(infrastructure, node_id),
            )
        ]

    if residual_nodes is None:
        return candidates

    service_attrs = _graph_node_attrs(application, service_id)
    return [
        node_id
        for node_id in candidates
        if node_id in residual_nodes
        and infrastructure.node_assets.satisfies(residual_nodes[node_id], service_attrs)
    ]


def _consume_residual(
    infrastructure: "Infrastructure",
    application: "Application",
    service_id: str,
    node_id: str,
    residual_nodes: Optional[Dict[str, Dict[str, Any]]],
) -> None:
    """Consume one service's requirements from the residual snapshot."""

    if residual_nodes is None or node_id not in residual_nodes:
        return

    service_attrs = _graph_node_attrs(application, service_id)
    residual_nodes[node_id] = infrastructure.node_assets.consume(
        residual_nodes[node_id],
        service_attrs,
    )


def _get_edge_latency(infrastructure: "Infrastructure", src_node: str, dst_node: str) -> float:
    """Return Eclypse aggregate path latency between two nodes."""
    if src_node == dst_node:
        return 0.1

    path_fn = getattr(infrastructure, "path", None)
    if not callable(path_fn):
        return float("inf")
    try:
        if path_fn(src_node, dst_node) is None:
            return float("inf")
    except Exception:
        return float("inf")

    if hasattr(infrastructure, "path_resources"):
        try:
            path_assets = infrastructure.path_resources(src_node, dst_node)
            latency = float(path_assets.get("latency", float("inf")))
            if math.isfinite(latency):
                return latency
        except Exception:
            return float("inf")

    return float("inf")


def _estimated_latency(infrastructure: "Infrastructure", src_node: str, dst_node: str) -> float:
    """Estimate latency between nodes when no direct path is known."""
    latency = _get_edge_latency(infrastructure, src_node, dst_node)
    if math.isfinite(latency) or src_node == dst_node:
        return latency
    return float("inf")


def _processing_cost(
    application: "Application",
    infrastructure: "Infrastructure",
    service_id: str,
    node_id: str,
) -> float:
    """Estimate processing cost for placing a service on a node."""
    service_data = _graph_node_attrs(application, service_id)
    node_data = _graph_node_attrs(infrastructure, node_id)

    base_processing = float(service_data.get("processing_time", service_data.get("cpu", 1.0)))
    node_speed = float(node_data.get("processing_time", 1.0))
    if node_speed <= 0:
        node_speed = 1.0

    service_cpu = float(service_data.get("cpu", 1.0))
    node_cpu = float(node_data.get("cpu", 1.0))
    cpu_penalty = service_cpu / max(node_cpu, 1e-6)

    return max(0.01, 0.5 * base_processing + 0.5 * cpu_penalty * node_speed)


def _stage_output_volume_mb(application: "Application", service_id: str) -> float:
    """Return the stage output volume in megabytes."""
    stage_data = _graph_node_attrs(application, service_id)
    output_size = float(
        stage_data.get(
            "output_size_bytes",
            stage_data.get("data_size", 250_000.0),
        )
    )
    return max(0.25, output_size / 1_000_000.0)


def _compute_cost_score(
    application: "Application",
    infrastructure: "Infrastructure",
    service_id: str,
    node_id: str,
) -> float:
    """Compute the base tier-weighted cost score for a placement."""
    layer_price = {
        "cloud": 1.0,
        "edge": 1.6,
        "iot": 4.0,
        "space": 6.0,
    }
    return _processing_cost(application, infrastructure, service_id, node_id) * layer_price.get(
        _node_layer(infrastructure, node_id),
        2.0,
    )


def _predecessor_transfer_cost(
    application: "Application",
    infrastructure: "Infrastructure",
    service_id: str,
    node_id: str,
    mapping: Dict[str, str],
) -> float:
    """Sum transfer costs from already placed predecessors."""
    total = 0.0
    for predecessor in _neighbors(application, "predecessors", service_id):
        predecessor_node = mapping.get(predecessor)
        if predecessor_node is None:
            continue
        total += _stage_output_volume_mb(application, predecessor) * _estimated_latency(
            infrastructure,
            predecessor_node,
            node_id,
        )
    return total


def _deployment_cost_score(
    application: "Application",
    infrastructure: "Infrastructure",
    service_id: str,
    node_id: str,
    mapping: Dict[str, str],
) -> float:
    """Combine compute and predecessor transfer costs."""
    return _compute_cost_score(
        application,
        infrastructure,
        service_id,
        node_id,
    ) + _predecessor_transfer_cost(
        application,
        infrastructure,
        service_id,
        node_id,
        mapping,
    )


def _service_ids(application: "Application") -> Iterable[str]:
    """Return service IDs in application iteration order."""
    return list(getattr(application, "nodes", []))


def _neighbors(graph: Any, method_name: str, node_id: str) -> Iterable[str]:
    """Return neighbors from a graph method when available."""
    method = getattr(graph, method_name, None)
    if method is None:
        return ()
    return list(method(node_id))


def _topological_service_ids(application: "Application") -> List[str]:
    """Return services in topological order when possible."""
    ordered_ids = list(_service_ids(application))
    indegree = {service_id: 0 for service_id in ordered_ids}
    successors = {
        service_id: [
            successor
            for successor in _neighbors(application, "successors", service_id)
            if successor in indegree
        ]
        for service_id in ordered_ids
    }

    for service_id in ordered_ids:
        for successor in successors[service_id]:
            indegree[successor] += 1

    ready = [service_id for service_id in ordered_ids if indegree[service_id] == 0]
    result: List[str] = []
    while ready:
        service_id = ready.pop(0)
        result.append(service_id)
        for successor in successors[service_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)

    if len(result) != len(ordered_ids):
        result.extend(service_id for service_id in ordered_ids if service_id not in result)
    return result


def _placed_neighbor_count(
    application: "Application",
    service_id: str,
    mapping: Dict[str, str],
) -> int:
    """Count already placed neighbors of a service."""
    count = 0
    for predecessor in _neighbors(application, "predecessors", service_id):
        if predecessor in mapping:
            count += 1
    for successor in _neighbors(application, "successors", service_id):
        if successor in mapping:
            count += 1
    return count


def _candidate_preserves_placed_paths(
    application: "Application",
    infrastructure: "Infrastructure",
    service_id: str,
    node_id: str,
    mapping: Dict[str, str],
) -> bool:
    """Return whether a candidate keeps Eclypse paths to placed DAG neighbors."""
    for predecessor in _neighbors(application, "predecessors", service_id):
        predecessor_node = mapping.get(predecessor)
        if predecessor_node is not None and not math.isfinite(
            _estimated_latency(infrastructure, predecessor_node, node_id)
        ):
            return False

    for successor in _neighbors(application, "successors", service_id):
        successor_node = mapping.get(successor)
        if successor_node is not None and not math.isfinite(
            _estimated_latency(infrastructure, node_id, successor_node)
        ):
            return False

    return True


def _path_feasible_candidates(
    application: "Application",
    infrastructure: "Infrastructure",
    service_id: str,
    candidates: Iterable[str],
    mapping: Dict[str, str],
) -> List[str]:
    """Filter candidates through Eclypse path feasibility to placed neighbors."""
    candidate_list = list(candidates)
    if _placed_neighbor_count(application, service_id, mapping) == 0:
        return candidate_list
    return [
        node_id
        for node_id in candidate_list
        if _candidate_preserves_placed_paths(
            application,
            infrastructure,
            service_id,
            node_id,
            mapping,
        )
    ]


def _neighbor_latency_score(
    application: "Application",
    infrastructure: "Infrastructure",
    service_id: str,
    node_id: str,
    mapping: Dict[str, str],
) -> float:
    """Average latency from a candidate node to placed neighbors."""
    total = 0.0
    count = 0

    for predecessor in _neighbors(application, "predecessors", service_id):
        if predecessor in mapping:
            total += _estimated_latency(infrastructure, mapping[predecessor], node_id)
            count += 1

    for successor in _neighbors(application, "successors", service_id):
        if successor in mapping:
            total += _estimated_latency(infrastructure, node_id, mapping[successor])
            count += 1

    return total / count if count > 0 else 0.0


def _criticality_scores(application: "Application") -> Dict[str, int]:
    """Compute simple downstream criticality scores for services."""
    ordered_ids = _topological_service_ids(application)
    scores = {service_id: 0 for service_id in ordered_ids}
    for service_id in reversed(ordered_ids):
        successors = list(_neighbors(application, "successors", service_id))
        if successors:
            scores[service_id] = 1 + max(scores.get(successor, 0) for successor in successors)
    return scores


def _placement_frontier_nodes(
    application: "Application",
    service_id: str,
    mapping: Dict[str, str],
    direction: str,
) -> List[str]:
    """Return placed anchor nodes reachable in one graph direction."""
    frontier_nodes: List[str] = []
    visited = {service_id}
    pending = [service_id]

    while pending:
        current = pending.pop(0)
        for neighbor in _neighbors(application, direction, current):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            if _is_placement_constrained_service(application, neighbor):
                if neighbor in mapping:
                    frontier_nodes.append(mapping[neighbor])
                continue
            pending.append(neighbor)

    return list(dict.fromkeys(frontier_nodes))


def _one_sided_frontier_edge_bias(
    application: "Application",
    infrastructure: "Infrastructure",
    node_id: str,
    service_id: str,
    upstream_frontier: Iterable[str],
    downstream_frontier: Iterable[str],
) -> tuple[int, int]:
    """Return a tier preference bias for one-sided frontier stages."""
    one_sided_frontier = bool(list(upstream_frontier)) ^ bool(list(downstream_frontier))
    if not one_sided_frontier:
        return (1, 1)

    placement_layer = _placement_layer_for_service(application, service_id)
    if placement_layer == "cloud":
        return (
            0,
            _tier_rank(
                infrastructure,
                node_id,
                {"cloud": 0, "edge": 1, "iot": 2, "space": 3},
            ),
        )

    return (
        0,
        _tier_rank(
            infrastructure,
            node_id,
            {"edge": 0, "iot": 1, "cloud": 2, "space": 3},
        ),
    )


def _frontier_latency_score(
    infrastructure: "Infrastructure",
    node_id: str,
    upstream_frontier: Iterable[str],
    downstream_frontier: Iterable[str],
) -> float:
    """Sum latency from a node to the placement frontier."""
    upstream_score = sum(
        _estimated_latency(infrastructure, upstream_node, node_id)
        for upstream_node in upstream_frontier
    )
    downstream_score = sum(
        _estimated_latency(infrastructure, node_id, downstream_node)
        for downstream_node in downstream_frontier
    )
    return upstream_score + downstream_score


def _edge_candidates(
    infrastructure: "Infrastructure",
    candidates: Iterable[str],
) -> List[str]:
    """Filter candidates down to edge-layer nodes."""
    return [
        node_id
        for node_id in candidates
        if _node_matches_layer(
            node_id,
            "edge",
            node_attrs=_graph_node_attrs(infrastructure, node_id),
        )
    ]


def _tier_rank(
    infrastructure: "Infrastructure",
    node_id: str,
    order: Dict[str, int],
) -> int:
    """Return the rank of a node's layer in a tier order."""
    return order.get(_node_layer(infrastructure, node_id), len(order))


def _select_node(
    application: "Application",
    infrastructure: "Infrastructure",
    service_id: str,
    candidates: Iterable[str],
    mapping: Dict[str, str],
    tier_order: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    """Select the best node from a candidate set."""
    candidate_list = _path_feasible_candidates(
        application,
        infrastructure,
        service_id,
        candidates,
        mapping,
    )
    if not candidate_list:
        return None

    return min(
        candidate_list,
        key=lambda node_id: (
            _neighbor_latency_score(application, infrastructure, service_id, node_id, mapping),
            _tier_rank(infrastructure, node_id, tier_order or {}),
            _processing_cost(application, infrastructure, service_id, node_id),
            str(node_id),
        ),
    )


def _residual_fit_score(
    infrastructure: "Infrastructure",
    node_attrs: Dict[str, Any],
    service_attrs: Dict[str, Any],
) -> float:
    """Score a fit by remaining additive resources after placement."""

    remaining = infrastructure.node_assets.consume(node_attrs, service_attrs)
    score = 0.0
    for resource in ("cpu", "ram", "storage"):
        try:
            score += max(0.0, float(remaining.get(resource, 0.0)))
        except (TypeError, ValueError):
            continue
    return score


class BestFitStrategy(EclypseBestFitStrategy):
    """Eclypse-native best-fit with deterministic residual-resource consumption."""

    def place(
        self,
        infrastructure: "Infrastructure",
        application: "Application",
        placements: Dict[str, "Placement"],
        placement_view: Optional["PlacementView"],
    ) -> Dict[str, str]:
        """Place services on the tightest residual node that satisfies assets."""

        del placements

        if not self.is_feasible(infrastructure, application):
            return {}

        residual_nodes = _residual_node_state(infrastructure, placement_view)
        if residual_nodes is None:
            return super().place(infrastructure, application, {}, placement_view)

        mapping: Dict[str, str] = {}
        for service_id in _topological_service_ids(application):
            candidates = _candidate_nodes_for_service(
                infrastructure,
                application,
                service_id,
                residual_nodes=residual_nodes,
            )
            candidates = _path_feasible_candidates(
                application,
                infrastructure,
                service_id,
                candidates,
                mapping,
            )
            if not candidates:
                continue

            service_attrs = _graph_node_attrs(application, service_id)
            best_node = min(
                candidates,
                key=lambda node_id: (
                    _residual_fit_score(
                        infrastructure,
                        residual_nodes[node_id],
                        service_attrs,
                    ),
                    str(node_id),
                ),
            )
            mapping[service_id] = best_node
            _consume_residual(
                infrastructure,
                application,
                service_id,
                best_node,
                residual_nodes,
            )

        return mapping


class MinCTStrategy(PlacementStrategy):
    """Greedy minimum-completion-time placement strategy."""

    def __init__(self, latency_weight: float = 0.7, processing_weight: float = 0.3):
        """Store latency and processing weights for scoring."""
        self.latency_weight = latency_weight
        self.processing_weight = processing_weight

    def place(
        self,
        infrastructure: "Infrastructure",
        application: "Application",
        placements: Dict[str, "Placement"],
        placement_view: Optional["PlacementView"],
    ) -> Dict[str, str]:
        """Place services by minimizing weighted latency and processing cost."""
        del placements

        residual_nodes = _residual_node_state(infrastructure, placement_view)

        if not self.is_feasible(infrastructure, application):
            return {}

        mapping: Dict[str, str] = {}
        for service_id in _service_ids(application):
            candidates = _candidate_nodes_for_service(
                infrastructure,
                application,
                service_id,
                residual_nodes=residual_nodes,
            )
            if not candidates:
                continue

            best_node: Optional[str] = None
            best_score = float("inf")
            for node_id in candidates:
                processing_time = _processing_cost(application, infrastructure, service_id, node_id)
                latency_score = 0.0
                dependency_count = 0

                for predecessor in _neighbors(application, "predecessors", service_id):
                    if predecessor in mapping:
                        latency_score += _get_edge_latency(infrastructure, mapping[predecessor], node_id)
                        dependency_count += 1

                for successor in _neighbors(application, "successors", service_id):
                    if successor in mapping:
                        latency_score += _get_edge_latency(infrastructure, node_id, mapping[successor])
                        dependency_count += 1

                if dependency_count > 0:
                    latency_score /= dependency_count

                score = self.processing_weight * processing_time + self.latency_weight * latency_score
                if score < best_score:
                    best_score = score
                    best_node = node_id

            if best_node is not None:
                mapping[service_id] = best_node
                _consume_residual(
                    infrastructure,
                    application,
                    service_id,
                    best_node,
                    residual_nodes,
                )

        return mapping


class RoundRobinStrategy(EclypseRoundRobinStrategy):
    """Eclypse-native round-robin with benchmark anchor extensions."""

    def place(
        self,
        infrastructure: "Infrastructure",
        application: "Application",
        placements: Dict[str, "Placement"],
        placement_view: Optional["PlacementView"],
    ) -> Dict[str, str]:
        """Assign each service to the next feasible candidate node."""
        if not self.is_feasible(infrastructure, application):
            return {}

        service_ids = list(_service_ids(application))
        if self._can_use_native_round_robin(infrastructure, application, service_ids):
            return super().place(infrastructure, application, placements, placement_view)

        residual_nodes = _residual_node_state(infrastructure, placement_view)
        mapping: Dict[str, str] = {}
        round_robin_index = 0
        for service_id in service_ids:
            candidates = _candidate_nodes_for_service(
                infrastructure,
                application,
                service_id,
                residual_nodes=residual_nodes,
            )
            if not candidates:
                continue
            mapping[service_id] = candidates[round_robin_index % len(candidates)]
            _consume_residual(
                infrastructure,
                application,
                service_id,
                mapping[service_id],
                residual_nodes,
            )
            round_robin_index += 1

        return mapping

    def _can_use_native_round_robin(
        self,
        infrastructure: "Infrastructure",
        application: "Application",
        service_ids: List[str],
    ) -> bool:
        """Return whether Eclypse's built-in round-robin can handle this case."""
        available_nodes = _available_nodes(infrastructure)
        if len(service_ids) > len(available_nodes):
            return False
        for service_id in service_ids:
            if _placement_layer_for_service(application, service_id) is not None:
                return False
            service_attrs = _graph_node_attrs(application, service_id)
            if any(float(service_attrs.get(resource, 0.0) or 0.0) > 0.0 for resource in ("cpu", "ram", "storage")):
                return False
        return True


class NearestFeasibleEdgeStrategy(PlacementStrategy):
    """Prefer edge nodes when possible, with anchor-safe fallback."""

    def place(
        self,
        infrastructure: "Infrastructure",
        application: "Application",
        placements: Dict[str, "Placement"],
        placement_view: Optional["PlacementView"],
    ) -> Dict[str, str]:
        """Place services near the edge when feasible."""
        del placements

        residual_nodes = _residual_node_state(infrastructure, placement_view)

        if not self.is_feasible(infrastructure, application):
            return {}

        mapping: Dict[str, str] = {}
        for service_id in _topological_service_ids(application):
            candidates = _candidate_nodes_for_service(
                infrastructure,
                application,
                service_id,
                residual_nodes=residual_nodes,
            )
            if not candidates:
                continue

            preferred_candidates = _edge_candidates(infrastructure, candidates) or candidates
            best_node = _select_node(
                application,
                infrastructure,
                service_id,
                preferred_candidates,
                mapping,
                tier_order={"edge": 0, "iot": 1, "cloud": 2, "space": 3},
            )
            if best_node is not None:
                mapping[service_id] = best_node
                _consume_residual(
                    infrastructure,
                    application,
                    service_id,
                    best_node,
                    residual_nodes,
                )

        return mapping


class CostGreedyStrategy(PlacementStrategy):
    """Choose the cheapest feasible tier under the benchmark tier model."""

    def place(
        self,
        infrastructure: "Infrastructure",
        application: "Application",
        placements: Dict[str, "Placement"],
        placement_view: Optional["PlacementView"],
    ) -> Dict[str, str]:
        """Place services on the lowest-cost feasible nodes."""
        del placements

        residual_nodes = _residual_node_state(infrastructure, placement_view)

        if not self.is_feasible(infrastructure, application):
            return {}

        mapping: Dict[str, str] = {}

        for service_id in _topological_service_ids(application):
            candidates = _candidate_nodes_for_service(
                infrastructure,
                application,
                service_id,
                residual_nodes=residual_nodes,
            )
            if not candidates:
                continue

            best_node = min(
                candidates,
                key=lambda node_id: (
                    _deployment_cost_score(application, infrastructure, service_id, node_id, mapping),
                    _predecessor_transfer_cost(application, infrastructure, service_id, node_id, mapping),
                    _compute_cost_score(application, infrastructure, service_id, node_id),
                    _tier_rank(
                        infrastructure,
                        node_id,
                        {"edge": 0, "cloud": 1, "iot": 2, "space": 3},
                    ),
                    str(node_id),
                ),
            )
            mapping[service_id] = best_node
            _consume_residual(
                infrastructure,
                application,
                service_id,
                best_node,
                residual_nodes,
            )

        return mapping


class DependencyAwareStrategy(PlacementStrategy):
    """Place anchor stages first, then expand outward using DAG adjacency."""

    def place(
        self,
        infrastructure: "Infrastructure",
        application: "Application",
        placements: Dict[str, "Placement"],
        placement_view: Optional["PlacementView"],
    ) -> Dict[str, str]:
        """Place placement-constrained services first, then fill remaining stages."""
        del placements

        residual_nodes = _residual_node_state(infrastructure, placement_view)

        if not self.is_feasible(infrastructure, application):
            return {}

        ordered_ids = _topological_service_ids(application)
        mapping: Dict[str, str] = {}
        placement_tier_order = {"edge": 0, "cloud": 1, "iot": 2, "space": 3}

        for service_id in ordered_ids:
            if _placement_layer_for_service(application, service_id) is None:
                continue

            candidates = _candidate_nodes_for_service(
                infrastructure,
                application,
                service_id,
                residual_nodes=residual_nodes,
            )
            best_node = _select_node(
                application,
                infrastructure,
                service_id,
                candidates,
                mapping,
                tier_order=placement_tier_order,
            )
            if best_node is not None:
                mapping[service_id] = best_node
                _consume_residual(
                    infrastructure,
                    application,
                    service_id,
                    best_node,
                    residual_nodes,
                )

        for service_id in ordered_ids:
            if service_id in mapping:
                continue
            candidates = _candidate_nodes_for_service(
                infrastructure,
                application,
                service_id,
                residual_nodes=residual_nodes,
            )
            if not candidates:
                continue

            upstream_frontier = _placement_frontier_nodes(
                application,
                service_id,
                mapping,
                "predecessors",
            )
            downstream_frontier = _placement_frontier_nodes(
                application,
                service_id,
                mapping,
                "successors",
            )
            bridge_stage = bool(upstream_frontier and downstream_frontier)

            best_node = min(
                candidates,
                key=lambda node_id: (
                    _one_sided_frontier_edge_bias(
                        application,
                        infrastructure,
                        node_id,
                        service_id,
                        upstream_frontier,
                        downstream_frontier,
                    ),
                    _frontier_latency_score(
                        infrastructure,
                        node_id,
                        upstream_frontier,
                        downstream_frontier,
                    ),
                    0
                    if bridge_stage and _node_matches_layer(
                        node_id,
                        "edge",
                        node_attrs=_graph_node_attrs(infrastructure, node_id),
                    )
                    else 1,
                    _tier_rank(
                        infrastructure,
                        node_id,
                        {"edge": 0, "iot": 1, "cloud": 2, "space": 3},
                    ),
                    _processing_cost(application, infrastructure, service_id, node_id),
                    str(node_id),
                ),
            )
            mapping[service_id] = best_node
            _consume_residual(
                infrastructure,
                application,
                service_id,
                best_node,
                residual_nodes,
            )

        return mapping


CriticalPathStrategy = DependencyAwareStrategy


def build_placement_strategy(name: str):
    """Return the placement strategy instance for a strategy name, normalizing common aliases.

    Shared by the autoscaling, joint, and rule-based controllers so the core strategy mapping lives
    in one place. Raises ``ValueError`` on an unknown name. The placement-only controller keeps its
    own resolver because it additionally accepts ``round_robin``, ``milp``, ``static``, and the
    extreme ``all_*`` baselines.
    """

    key = str(name).lower().replace("-", "_")
    if key in {"best_fit", "bestfit"}:
        return BestFitStrategy()
    if key in {"min_latency", "min_ct", "heft_like"}:
        return MinCTStrategy()
    if key == "nearest_feasible_edge":
        return NearestFeasibleEdgeStrategy()
    if key == "cost_greedy":
        return CostGreedyStrategy()
    if key in {"dependency_aware", "critical_path"}:
        return DependencyAwareStrategy()
    raise ValueError(
        "placement_strategy must be one of: best_fit, min_latency, nearest_feasible_edge, "
        "cost_greedy, dependency_aware, critical_path"
    )


__all__ = [
    "BestFitStrategy",
    "CostGreedyStrategy",
    "CriticalPathStrategy",
    "DependencyAwareStrategy",
    "MinCTStrategy",
    "NearestFeasibleEdgeStrategy",
    "RoundRobinStrategy",
    "build_placement_strategy",
]
