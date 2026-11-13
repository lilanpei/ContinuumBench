from __future__ import annotations

import math
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
class _PlacementReplanMixin:
    """Shared Eclypse-native placement replanning helper for autoscaling baselines."""

    placement_strategy: str = "best_fit"
    replan_every_epochs: int = 3
    # Ablation knobs (defaults reproduce the coupled behavior): whether a scale event triggers a
    # re-place, and whether time-driven periodic replanning runs at all. Setting both False gives an
    # autoscaler with placement frozen after the initial plan.
    replan_on_scale: bool = True
    periodic_replan: bool = True

    def __post_init__(self) -> None:
        """Instantiate the placement strategy selected for this baseline."""

        self._strategy = self._build_strategy(self.placement_strategy)

    def _maybe_replan(
        self,
        state: ControllerState,
        scale_actions: List[ScaleAction],
    ) -> tuple[Optional[Dict[str, str]], bool, bool, List[ScaleAction]]:
        """Return a placement mapping when the baseline should replan."""

        active_services = self._project_active_services(state, scale_actions)
        full_replan = (
            not state.placement
            or (self.replan_on_scale and bool(scale_actions))
            or (self.periodic_replan and state.epoch % max(1, int(self.replan_every_epochs)) == 0)
        )
        if not full_replan:
            # Decoupled placement: freeze existing assignments. A scale-up still activates new
            # workers that MUST be placed (the runner requires a complete placement), so place only
            # those incrementally and keep every existing worker on its current node (no migration).
            new_services = [s for s in active_services if s not in state.placement]
            if not new_services:
                return None, False, False, scale_actions
            mapping = self._plan_placement(state, active_services)
            if mapping:
                frozen = {s: state.placement[s] for s in active_services if s in state.placement}
                merged = {**mapping, **frozen}
                if mapping_fits_node_capacity(state, merged):
                    return merged, True, False, scale_actions
            # The new workers do not fit around the frozen placement. Rather than defer the
            # scale-up forever (a pool can stay stuck at its floor under a tight budget), fall
            # back to one full re-solve, which may migrate existing workers.
            full = self._plan_placement(state, active_services)
            if full and mapping_fits_node_capacity(state, full):
                return full, True, False, scale_actions
            return None, False, True, []

        placement = self._plan_placement(state, active_services)
        if not placement:
            if state.placement:
                return None, False, True, []
            raise ValueError(
                f"placement strategy '{self.placement_strategy}' could not place active services"
            )
        return placement, True, False, scale_actions

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
            k = desired.get(stage, snapshot.active_workers)
            k = max(0, min(len(workers_sorted), int(k)))
            active_set.difference_update(workers_sorted)
            active_set.update(workers_sorted[:k])

        return [service_id for service_id in state.application.nodes if service_id in active_set]

    def _active_services(self, state: ControllerState) -> List[str]:
        """Return services active in the current worker-pool state."""

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
        """Create the Eclypse-native placement strategy used by this baseline."""

        if str(strategy).lower().replace("-", "_") in {"milp", "solver_milp"}:
            return None  # MILP is solved per-replan via milp_plan_placement, not an Eclypse strategy
        return build_placement_strategy(strategy)

    def _plan_placement(self, state: ControllerState, active_services: List[str]) -> Dict[str, str]:
        """Compute a placement, dispatching to the MILP global re-optimizer when selected.

        With ``placement_strategy="milp"`` the planner is a genuinely re-optimizing (migrating)
        solver, so a coupled re-plan can move existing workers to the global optimum---unlike the
        order-stable heuristics, which reproduce the prior assignment and never migrate.
        """

        if str(self.placement_strategy).lower().replace("-", "_") in {"milp", "solver_milp"}:
            from continuum_bench.controllers.placement_only import milp_plan_placement

            mapping = milp_plan_placement(state, active_services)
            # The MILP oracle returns a raw mapping without the Eclypse substrate audit that the
            # heuristic path applies; under binding capacity its reduced model can over-subscribe a
            # node relative to the runner's full accounting. Guard here so the controller defers
            # gracefully (returns {}) instead of the runner hard-failing on enact.
            return mapping if mapping_fits_node_capacity(state, mapping) else {}
        return plan_placement(state, self._strategy, active_services)


@dataclass
class HPAController(_PlacementReplanMixin):
    """Kubernetes HPA-style autoscaler over benchmark worker pools.

    The desired replica count follows the core HPA ratio rule:
    ``ceil(current_replicas * current_utilization / target_utilization)``.
    Queue depth per active worker is used as the benchmark utilization signal.
    """

    target_utilization: float = 0.7
    tolerance: float = 0.1
    min_replicas: int = 1
    cooldown_epochs: int = 1

    _cooldowns: Dict[str, int] = field(default_factory=dict, init=False)

    def step(self, state: ControllerState) -> ControllerAction:
        """Return HPA-style scale and placement actions."""

        scale_actions: List[ScaleAction] = []
        pool_metrics: Dict[str, Dict[str, float]] = {}
        target_utilization = max(1e-9, float(self.target_utilization))

        for stage, snapshot in state.worker_pools.items():
            self._cooldowns[stage] = max(0, self._cooldowns.get(stage, 0) - 1)
            current = int(snapshot.active_workers)
            observed = float(state.queue_depths.get(stage, 0))
            utilization = observed / max(1.0, float(current))
            ratio = utilization / target_utilization

            desired = current
            min_replicas = max(snapshot.min_workers, int(self.min_replicas))
            if current <= 0 and observed > 0 and snapshot.max_workers > 0:
                desired = max(1, min_replicas)
                desired = min(snapshot.max_workers, int(desired))
            elif current > 0 and abs(1.0 - ratio) > max(0.0, float(self.tolerance)):
                desired = math.ceil(current * ratio)
                desired = max(min_replicas, min(snapshot.max_workers, int(desired)))
            elif observed <= 0:
                desired = max(snapshot.min_workers, min(snapshot.max_workers, int(desired)))

            pool_metrics[stage] = {
                "observed_utilization": float(utilization),
                "target_utilization": float(target_utilization),
                "utilization_ratio": float(ratio),
                "desired_workers": float(desired),
                "min_replicas": float(min_replicas),
            }

            if self._cooldowns[stage] == 0 and desired != current:
                scale_actions.append(ScaleAction(stage=stage, active_workers=desired))
                self._cooldowns[stage] = max(0, int(self.cooldown_epochs))

        placement, replan, replan_deferred, scale_actions = self._maybe_replan(
            state,
            scale_actions,
        )
        return ControllerAction(
            scale=scale_actions,
            placement=placement,
            metadata={
                "controller": "hpa",
                "placement_strategy": self.placement_strategy,
                "replan": replan,
                "replan_deferred": replan_deferred,
                "pool_metrics": pool_metrics,
            },
        )


@dataclass
class KEDAController(_PlacementReplanMixin):
    """KEDA-style event-driven autoscaler over benchmark queue triggers."""

    queue_target_per_worker: int = 10
    activation_queue_length: int = 1
    min_replicas: int = 0
    cooldown_epochs: int = 2

    _cooldowns: Dict[str, int] = field(default_factory=dict, init=False)

    def step(self, state: ControllerState) -> ControllerAction:
        """Return KEDA-style queue-trigger scale and placement actions."""

        scale_actions: List[ScaleAction] = []
        trigger_metrics: Dict[str, Dict[str, object]] = {}
        target_per_worker = max(1, int(self.queue_target_per_worker))

        for stage, snapshot in state.worker_pools.items():
            self._cooldowns[stage] = max(0, self._cooldowns.get(stage, 0) - 1)
            queue_length = int(state.queue_depths.get(stage, 0))
            trigger_active = queue_length >= max(1, int(self.activation_queue_length))
            min_replicas = max(snapshot.min_workers, int(self.min_replicas))

            if trigger_active:
                desired = math.ceil(queue_length / target_per_worker)
                desired = max(min_replicas, desired)
            else:
                desired = min_replicas
            desired = max(snapshot.min_workers, min(snapshot.max_workers, int(desired)))

            trigger_metrics[stage] = {
                "queue_length": int(queue_length),
                "queue_target_per_worker": int(target_per_worker),
                "activation_queue_length": int(self.activation_queue_length),
                "trigger_active": bool(trigger_active),
                "desired_workers": int(desired),
            }

            if self._cooldowns[stage] == 0 and desired != snapshot.active_workers:
                scale_actions.append(ScaleAction(stage=stage, active_workers=desired))
                self._cooldowns[stage] = max(0, int(self.cooldown_epochs))

        placement, replan, replan_deferred, scale_actions = self._maybe_replan(
            state,
            scale_actions,
        )
        return ControllerAction(
            scale=scale_actions,
            placement=placement,
            metadata={
                "controller": "keda",
                "placement_strategy": self.placement_strategy,
                "replan": replan,
                "replan_deferred": replan_deferred,
                "trigger_metrics": trigger_metrics,
            },
        )
