from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from continuum_bench.controllers.interfaces import (
    ControllerAction,
    ControllerState,
    ScaleAction,
)
from continuum_bench.controllers.placement_views import mapping_fits_node_capacity, plan_placement
from continuum_bench.placement.strategies import build_placement_strategy


@dataclass
class SeparatedLoopsController:
    """Separated-loops baseline: threshold autoscale + periodic placement."""

    placement_strategy: str = "best_fit"
    place_every_epochs: int = 3
    replan_on_scale: bool = True
    periodic_replan: bool = True
    queue_up_threshold: int = 20
    queue_down_threshold: int = 5
    age_up_threshold_s: float = 20.0
    age_down_threshold_s: float = 5.0
    ttl_pressure_up_threshold: float = 0.25
    ttl_pressure_down_threshold: float = 0.05
    cooldown_epochs: int = 2
    scale_step: int = 1

    _cooldowns: Dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self):
        """Instantiate the configured placement strategy."""
        self._strategy = self._build_strategy(self.placement_strategy)

    def step(self, state: ControllerState) -> ControllerAction:
        """Run threshold-based scaling and optional placement replanning."""
        pool_queue_depths = state.queue_depths
        pool_oldest_age_s = state.oldest_age_s
        pool_ttl_pressure = state.ttl_pressure

        scale_actions: List[ScaleAction] = []
        for stage, snapshot in state.worker_pools.items():
            self._cooldowns[stage] = max(0, self._cooldowns.get(stage, 0) - 1)

            queue_depth = int(pool_queue_depths.get(stage, 0))
            oldest_age = float(pool_oldest_age_s.get(stage, 0.0))
            ttl_pressure = float(pool_ttl_pressure.get(stage, 0.0))

            current = snapshot.active_workers
            target = current

            if self._cooldowns[stage] == 0:
                scale_up = (
                    queue_depth >= self.queue_up_threshold
                    or oldest_age >= self.age_up_threshold_s
                    or ttl_pressure >= self.ttl_pressure_up_threshold
                )
                scale_down = (
                    queue_depth <= self.queue_down_threshold
                    and oldest_age <= self.age_down_threshold_s
                    and ttl_pressure <= self.ttl_pressure_down_threshold
                )

                if scale_up and current < snapshot.max_workers:
                    target = min(snapshot.max_workers, current + max(1, self.scale_step))
                elif scale_down and current > snapshot.min_workers:
                    target = max(snapshot.min_workers, current - max(1, self.scale_step))

            if target != current:
                scale_actions.append(ScaleAction(stage=stage, active_workers=target))
                self._cooldowns[stage] = max(0, int(self.cooldown_epochs))

        active_services = self._project_active_services(state, scale_actions)
        full_replan = (
            not state.placement
            or (self.replan_on_scale and bool(scale_actions))
            or (self.periodic_replan and state.epoch % max(1, self.place_every_epochs) == 0)
        )

        placement: Optional[Dict[str, str]] = None
        replan = full_replan
        replan_deferred = False
        if full_replan:
            placement = self._plan_placement(state, active_services)
            if not placement:
                if state.placement:
                    scale_actions = []
                    replan = False
                    replan_deferred = True
                    placement = None
                else:
                    raise ValueError(
                        f"placement strategy '{self.placement_strategy}' could not place active services"
                    )
        else:
            # Decoupled: freeze existing assignments, but place any newly-activated workers so the
            # runner still receives a complete placement (existing workers never migrate).
            new_services = [s for s in active_services if s not in state.placement]
            if new_services:
                mapping = self._plan_placement(state, active_services)
                frozen = {s: state.placement[s] for s in active_services if s in state.placement}
                merged = {**mapping, **frozen} if mapping else {}
                if merged and mapping_fits_node_capacity(state, merged):
                    placement = merged
                    replan = True
                else:
                    scale_actions = []
                    replan_deferred = True

        return ControllerAction(
            scale=scale_actions,
            placement=placement,
            metadata={
                "controller": "separated_loops",
                "placement_strategy": self.placement_strategy,
                "replan": replan,
                "replan_deferred": replan_deferred,
            },
        )

    def _project_active_services(
        self,
        state: ControllerState,
        scale_actions: List[ScaleAction],
    ) -> List[str]:
        """Project active services after applying pending scale actions."""
        active_set = set(self._active_services(state))
        desired = {action.stage: action.active_workers for action in scale_actions}

        for stage, snapshot in state.worker_pools.items():
            workers_sorted = list(snapshot.active_worker_ids) + list(snapshot.inactive_worker_ids)
            k = desired.get(stage, state.worker_pools[stage].active_workers)
            k = max(0, min(len(workers_sorted), int(k)))
            active_set.difference_update(workers_sorted)
            active_set.update(workers_sorted[:k])

        return [service_id for service_id in state.application.nodes if service_id in active_set]

    def _active_services(self, state: ControllerState) -> List[str]:
        """Return services that are currently active."""
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

    def _build_strategy(self, strategy: str):
        """Create the configured placement strategy instance."""

        if str(strategy).lower().replace("-", "_") in {"milp", "solver_milp"}:
            return None  # MILP is solved per-replan via milp_plan_placement, not an Eclypse strategy
        return build_placement_strategy(strategy)

    def _plan_placement(self, state: ControllerState, active_services: List[str]) -> Dict[str, str]:
        """Compute a placement, dispatching to the MILP global re-optimizer when selected."""

        if str(self.placement_strategy).lower().replace("-", "_") in {"milp", "solver_milp"}:
            from continuum_bench.controllers.placement_only import milp_plan_placement

            mapping = milp_plan_placement(state, active_services)
            return mapping if mapping_fits_node_capacity(state, mapping) else {}
        return plan_placement(state, self._strategy, active_services)
