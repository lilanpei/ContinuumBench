from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, NamedTuple

from continuum_bench._eclypse import ensure_eclypse_log_level
from continuum_bench.controllers.placement_views import active_application_view

from eclypse.placement import Placement, PlacementManager


class _FixedPlacementStrategy:
    """Eclypse-compatible strategy that returns a fixed mapping."""

    def __init__(self, mapping: Dict[str, str]) -> None:
        self._mapping = dict(mapping)

    def place(
        self,
        infrastructure: Any,
        application: Any,
        placements: Any,
        placement_view: Any,
    ) -> Dict[str, str]:
        del infrastructure, application, placements, placement_view
        return dict(self._mapping)


class _AuditResult(NamedTuple):
    manager: PlacementManager
    placement: Placement
    generated: Dict[str, str]
    not_respected: List[str]
    reset_required: bool


class EclypsePlacementAdapter:
    """Own Eclypse placement objects for ContinuumBench runtime actions."""

    def __init__(self, infrastructure: Any, application: Any) -> None:
        ensure_eclypse_log_level()
        self.infrastructure = infrastructure
        self.application = application
        self.manager = PlacementManager(infrastructure=infrastructure)
        self.placement = Placement(
            infrastructure=infrastructure,
            application=application,
            strategy=None,
        )
        self.manager.placements[application.id] = self.placement
        self._last_active_services: tuple[str, ...] = ()

    @property
    def placement_view(self) -> Any:
        """Return Eclypse's current placement view snapshot."""

        return self.manager.placement_view

    def plan_mapping(
        self,
        *,
        strategy: Any,
        active_services: Iterable[str],
        node_overrides: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, str]:
        """Generate and audit a mapping through Eclypse's placement lifecycle."""

        active_service_ids = [service_id for service_id in active_services]
        active_application = active_application_view(
            self.application,
            active_service_ids,
            node_overrides=node_overrides,
        )
        audit = self._audit_mapping(
            active_application=active_application,
            strategy=strategy,
        )
        if not audit.generated or audit.not_respected or audit.reset_required:
            return {}
        return audit.generated

    def sync_mapping(
        self,
        mapping: Dict[str, str],
        *,
        active_services: Iterable[str],
        force: bool = False,
    ) -> Dict[str, str]:
        """Materialize a runtime mapping as an Eclypse placement and audit it."""

        active_service_ids = [service_id for service_id in active_services]
        active_service_key = tuple(active_service_ids)
        active_service_set = set(active_service_ids)
        pruned_mapping = {
            service_id: node_id
            for service_id, node_id in mapping.items()
            if service_id in active_service_set
        }
        if (
            not force
            and pruned_mapping == self.placement.mapping
            and active_service_key == self._last_active_services
        ):
            return dict(self.placement.mapping)

        missing = sorted(
            service_id
            for service_id in active_service_ids
            if service_id not in pruned_mapping
        )
        invalid = sorted(
            service_id
            for service_id, node_id in pruned_mapping.items()
            if node_id not in self.infrastructure.nodes
        )
        errors = []
        if missing:
            errors.append(f"missing placements for {missing}")
        if invalid:
            errors.append(f"unknown placement nodes for {invalid}")
        if errors:
            raise ValueError(
                "Controller placement is incomplete or invalid; "
                "ContinuumBench does not synthesize substrate placements locally: "
                + "; ".join(errors)
            )

        active_application = active_application_view(self.application, active_service_ids)
        audit = self._audit_mapping(
            active_application=active_application,
            strategy=_FixedPlacementStrategy(pruned_mapping),
        )
        if not audit.generated or audit.not_respected or audit.reset_required:
            reason = (
                f"{audit.not_respected}"
                if audit.not_respected
                else "service interaction path is not available"
            )
            raise ValueError(
                "Controller placement violates Eclypse substrate resources or paths: "
                f"{reason}"
            )

        self.manager = audit.manager
        self.placement = audit.placement
        self.manager.enact()
        self._last_active_services = active_service_key
        return dict(audit.generated)

    def demand_for_stage(self, node_id: str, stage_id: str) -> Optional[Dict[str, float]]:
        """Return Eclypse placement-view demand for one placed stage."""

        if self.placement.mapping.get(stage_id) != node_id:
            return None

        if not self.placement.application.has_node(stage_id):
            return None

        stage_attrs = self.placement.application.nodes[stage_id]
        return {
            resource: max(0.0, float(stage_attrs.get(resource, 0.0)))
            for resource in ("cpu", "ram", "storage")
        }

    def demands_for_node(self, node_id: str) -> Dict[str, Dict[str, float]]:
        """Return per-stage Eclypse placement demands reserved on one node."""

        return {
            stage_id: demand
            for stage_id, placed_node in self.placement.mapping.items()
            if placed_node == node_id
            and (demand := self.demand_for_stage(node_id, stage_id)) is not None
        }

    def reserved_demand_for_node(self, node_id: str) -> Dict[str, float]:
        """Return aggregate Eclypse PlacementView demand reserved on one node."""

        placement_view = getattr(self.manager, "placement_view", None)
        if placement_view is None or not hasattr(placement_view, "get_node_view"):
            return {resource: 0.0 for resource in ("cpu", "ram", "storage")}
        node_view = placement_view.get_node_view(node_id)
        return {
            resource: max(0.0, float(node_view.get(resource, 0.0)))
            for resource in ("cpu", "ram", "storage")
        }

    def _audit_mapping(
        self,
        *,
        active_application: Any,
        strategy: Any,
    ) -> _AuditResult:
        """Run an Eclypse placement audit for one active application."""

        manager = PlacementManager(infrastructure=self.infrastructure)
        placement = Placement(
            infrastructure=self.infrastructure,
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
        generated = dict(placement.mapping)
        return _AuditResult(
            manager=manager,
            placement=placement,
            generated=generated,
            not_respected=not_respected,
            reset_required=bool(placement._to_reset),
        )
