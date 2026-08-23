"""Feature-rule controller (paper name: "Feature-rule"; artifact/config key: ``learned_stub``).

Named ``learned_stub`` only for compatibility with the committed sweep manifests and result CSVs,
this controller does NOT learn: it is a deterministic, dependency-free policy over normalized
benchmark features. The companion ``rl_meta`` controller is the paper's "Meta-rule" baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from continuum_bench.controllers.interfaces import (
    ControllerAction,
    ControllerState,
    ScaleAction,
)
from continuum_bench.controllers.placement_views import plan_placement
from continuum_bench.placement.strategies import build_placement_strategy


@dataclass
class LearnedStubController:
    """Feature-rule controller baseline (paper name: "Feature-rule").

    This controller intentionally avoids ML runtime dependencies while behaving
    like a policy inside the simulation loop: it observes ``ControllerState``,
    computes normalized pressure features, emits scale/placement actions, and
    leaves reward computation to the trajectory logger. It is an untrained
    rule-based policy baseline, not a learned-control result.
    """

    placement_strategy: str = "best_fit"
    replan_every_epochs: int = 3
    queue_weight: float = 1.0
    age_weight: float = 1.0
    ttl_weight: float = 1.0
    scale_up_score: float = 1.0
    scale_down_score: float = 0.15
    scale_step: int = 1
    cooldown_epochs: int = 1

    _cooldowns: Dict[str, int] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        """Instantiate the placement policy used by the feature controller."""

        self._strategy = self._build_strategy(self.placement_strategy)

    def step(self, state: ControllerState) -> ControllerAction:
        """Return scale and placement actions from the rule-based policy surface."""

        scale_actions: List[ScaleAction] = []
        feature_log: Dict[str, Dict[str, float]] = {}

        for stage, snapshot in state.worker_pools.items():
            self._cooldowns[stage] = max(0, self._cooldowns.get(stage, 0) - 1)
            features = self._features_for_stage(state, stage, snapshot.active_workers)
            feature_log[stage] = dict(features)

            target = snapshot.active_workers
            if self._cooldowns[stage] == 0:
                if features["score"] >= self.scale_up_score and target < snapshot.max_workers:
                    target = min(snapshot.max_workers, target + max(1, int(self.scale_step)))
                elif (
                    features["score"] <= self.scale_down_score
                    and target > snapshot.min_workers
                ):
                    target = max(snapshot.min_workers, target - max(1, int(self.scale_step)))

            if target != snapshot.active_workers:
                scale_actions.append(ScaleAction(stage=stage, active_workers=target))
                self._cooldowns[stage] = max(0, int(self.cooldown_epochs))

        replan = (
            not state.placement
            or bool(scale_actions)
            or state.epoch % max(1, int(self.replan_every_epochs)) == 0
        )

        placement: Optional[Dict[str, str]] = None
        replan_deferred = False
        if replan:
            active_services = self._project_active_services(state, scale_actions)
            placement = plan_placement(state, self._strategy, active_services)
            if not placement:
                if state.placement:
                    scale_actions = []
                    replan = False
                    replan_deferred = True
                    placement = None
                else:
                    raise ValueError(
                        f"learned_stub placement strategy '{self.placement_strategy}' "
                        "could not place active services"
                    )

        return ControllerAction(
            scale=scale_actions,
            placement=placement,
            metadata={
                "controller": "learned_stub",
                "policy": "linear_feature_policy",
                "placement_strategy": self.placement_strategy,
                "replan": replan,
                "replan_deferred": replan_deferred,
                "reward_logging": True,
                "heuristic_delegate": False,
                "policy_features": feature_log,
            },
        )

    def _features_for_stage(
        self,
        state: ControllerState,
        stage: str,
        active_workers: int,
    ) -> Dict[str, float]:
        """Compute the feature score used by the lightweight policy baseline."""

        worker_denominator = max(1.0, float(active_workers))
        queue_pressure = float(state.queue_depths.get(stage, 0)) / worker_denominator
        age_pressure = float(state.oldest_age_s.get(stage, 0.0)) / worker_denominator
        ttl_pressure = max(0.0, float(state.ttl_pressure.get(stage, 0.0)))
        score = (
            self.queue_weight * queue_pressure
            + self.age_weight * age_pressure
            + self.ttl_weight * ttl_pressure
        )
        return {
            "queue_pressure": float(queue_pressure),
            "age_pressure": float(age_pressure),
            "ttl_pressure": float(ttl_pressure),
            "score": float(score),
        }

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
        """Create the Eclypse-native placement strategy used by the policy."""

        return build_placement_strategy(strategy)
