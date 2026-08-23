from __future__ import annotations

from typing import Any, Dict, List, Optional

from continuum_bench._eclypse import ensure_eclypse_log_level

from eclypse.placement import Placement, PlacementManager


def active_application_view(
    application: Any,
    active_ids: List[str],
    node_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Any:
    """Return an active-service Eclypse application graph."""

    if not _is_eclypse_application(application):
        raise TypeError("Controller placement requires an Eclypse Application graph.")
    return _copy_active_eclypse_application(application, active_ids, node_overrides=node_overrides)


def plan_placement(
    state: Any,
    strategy: Any,
    active_services: List[str],
) -> Dict[str, str]:
    """Plan placement through Eclypse's Placement/PlacementManager lifecycle."""

    node_overrides = _active_service_node_overrides(state, active_services)
    planner = state.metadata.get("placement_planner") if hasattr(state, "metadata") else None
    if callable(planner):
        return planner(
            strategy=strategy,
            active_services=active_services,
            node_overrides=node_overrides,
        )

    return plan_mapping_with_eclypse(
        infrastructure=state.infrastructure,
        application=state.application,
        active_services=active_services,
        strategy=strategy,
        node_overrides=node_overrides,
    )


def plan_mapping_with_eclypse(
    *,
    infrastructure: Any,
    application: Any,
    active_services: List[str],
    strategy: Any,
    node_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, str]:
    """Generate and audit one mapping with Eclypse placement primitives."""

    ensure_eclypse_log_level()
    if not all(hasattr(infrastructure, attr) for attr in ("node_assets", "edge_assets")):
        raise TypeError("Controller placement requires an Eclypse Infrastructure graph.")
    if not _is_eclypse_application(application):
        raise TypeError("Controller placement requires an Eclypse Application graph.")

    active_application = active_application_view(
        application,
        list(active_services),
        node_overrides=node_overrides,
    )
    manager = PlacementManager(infrastructure=infrastructure)
    placement = Placement(
        infrastructure=infrastructure,
        application=active_application,
        strategy=strategy,
    )
    manager.placements[active_application.id] = placement

    violations = list(manager.mapping_phase())
    not_respected = sorted(
        {
            node_id
            for _placement, nodes in violations
            for node_id in nodes
        }
    )
    if not placement.mapping or placement._to_reset or not_respected:
        return {}

    manager.enact()
    return dict(placement.mapping)


def mapping_fits_node_capacity(state: Any, mapping: Dict[str, str]) -> bool:
    """Return whether a placement mapping respects per-node cpu/ram/storage capacity.

    A lightweight feasibility check for mappings produced outside the Eclypse audit (e.g. the MILP
    oracle), letting a controller defer gracefully instead of the runner hard-failing on an
    over-subscribed node. Approximate (it ignores runtime overheads), so it only rejects gross
    over-subscription.
    """

    if not mapping:
        return False
    meta = getattr(state, "metadata", {}) or {}
    base = meta.get("service_base_attrs", {}) if isinstance(meta, dict) else {}
    app_nodes = state.application.nodes
    usage: Dict[str, Dict[str, float]] = {}
    for service_id, node_id in mapping.items():
        attrs = dict(app_nodes.get(service_id, {}))
        attrs.update(dict(base.get(service_id, {})))
        bucket = usage.setdefault(str(node_id), {"cpu": 0.0, "ram": 0.0, "storage": 0.0})
        for dim in ("cpu", "ram", "storage"):
            bucket[dim] += float(attrs.get(dim, 0.0))
    nodes_attr = getattr(state.infrastructure, "nodes", None)
    if not callable(nodes_attr):
        return True
    caps = {str(node_id): attrs for node_id, attrs in nodes_attr(data=True)}
    for node_id, used in usage.items():
        cap = caps.get(node_id, {})
        for dim in ("cpu", "ram", "storage"):
            if used[dim] > float(cap.get(dim, 0.0)) + 1e-6:
                return False
    return True


def _active_service_node_overrides(
    state: Any,
    active_services: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Return base service attributes for projected active workers."""

    base_attrs = dict(getattr(state, "metadata", {}).get("service_base_attrs", {}))
    if not base_attrs:
        return {}

    worker_ids = {
        worker_id
        for snapshot in getattr(state, "worker_pools", {}).values()
        for worker_id in [
            *getattr(snapshot, "active_worker_ids", []),
            *getattr(snapshot, "inactive_worker_ids", []),
        ]
    }
    active_set = set(active_services)
    return {
        service_id: dict(attrs)
        for service_id, attrs in base_attrs.items()
        if service_id in active_set and service_id in worker_ids
    }


def _is_eclypse_application(application: Any) -> bool:
    """Return whether an object looks like an Eclypse application graph."""

    return all(
        hasattr(application, attr)
        for attr in ("id", "node_assets", "edge_assets", "add_node", "add_edge")
    )


def _copy_active_eclypse_application(
    application: Any,
    active_ids: List[str],
    *,
    node_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Any:
    """Copy selected services and interactions into a fresh Eclypse Application."""

    active = application.__class__(
        application_id=f"{getattr(application, 'id', 'application')}_active",
        node_assets=dict(getattr(application, "_node_assets_builder", getattr(application, "node_assets", {}))),
        edge_assets=dict(getattr(application, "_edge_assets_builder", getattr(application, "edge_assets", {}))),
        include_default_assets=False,
    )
    active_set = set(active_ids)
    overrides = node_overrides or {}
    for service_id in active_ids:
        if service_id not in application.nodes:
            continue
        attrs = dict(application.nodes[service_id])
        attrs.update(dict(overrides.get(service_id, {})))
        active.add_node(service_id, strict=False, **attrs)

    edge_items = application.edges(data=True) if callable(application.edges) else []
    for src, dst, attrs in edge_items:
        if src in active_set and dst in active_set:
            active.add_edge(src, dst, strict=False, **dict(attrs))

    return active
