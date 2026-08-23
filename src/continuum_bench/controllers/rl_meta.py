"""Meta-rule controller (paper name: "Meta-rule"; artifact/config key: ``rl_meta``).

Despite the ``rl_meta`` / ``RLMeta*`` naming -- kept only for backward compatibility with the
committed sweep manifests and result CSVs -- this controller is a fully deterministic,
dependency-free rule-based baseline. It selects a discrete meta-action (scale worker pools
down/hold/up, whether to replan, and which ECLYPSE-audited placement strategy to use) from fixed
thresholds. It performs NO learning and uses NO reward signal or policy gradient; the discrete
action space and the ``rl_ready`` action metadata exist only so that a future learned policy can
be slotted in unchanged. The companion ``learned_stub`` controller is the paper's "Feature-rule"
baseline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from continuum_bench.controllers.interfaces import (
    ControllerAction,
    ControllerState,
    ScaleAction,
)
from continuum_bench.controllers.placement_views import plan_placement
from continuum_bench.placement.strategies import build_placement_strategy


_VALID_SCALE_DIRECTIONS = {"down", "hold", "up"}


@dataclass(frozen=True)
class RLMetaAction:
    """Discrete meta-action (scale direction, replan flag, placement strategy).

    Produced by the deterministic ``RLMetaController`` (the paper's Meta-rule baseline). The action
    space is shaped for a future learned policy but is selected here by fixed rules, not learning.
    """

    scale_direction: str = "hold"
    replan: bool = False
    placement_strategy: str = "best_fit"

    def __post_init__(self) -> None:
        """Validate the discrete action fields."""

        direction = self.scale_direction.lower().replace("-", "_")
        if direction not in _VALID_SCALE_DIRECTIONS:
            raise ValueError("scale_direction must be one of: down, hold, up")
        object.__setattr__(self, "scale_direction", direction)
        object.__setattr__(self, "placement_strategy", _normalize_strategy_name(self.placement_strategy))

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-friendly action description."""

        return {
            "scale_direction": self.scale_direction,
            "replan": bool(self.replan),
            "placement_strategy": self.placement_strategy,
        }


@dataclass
class RLMetaController:
    """Deterministic Meta-rule controller baseline (paper name: "Meta-rule").

    Named ``rl_meta`` only for compatibility with the committed sweep manifests and result CSVs,
    this controller does NOT learn. It exposes a small discrete action space -- scale worker pools
    down/hold/up, decide whether to replan, and choose one ECLYPSE-audited placement strategy --
    and selects an action from fixed thresholds. The action space is shaped so a future learned
    policy could replace the rule policy unchanged, while ``scripted`` makes tests and offline
    policy playback straightforward.
    """

    placement_strategies: Sequence[str] = ("best_fit", "min_latency", "cost_greedy")
    replan_every_epochs: int = 3
    policy: str = "threshold"
    action_sequence: Sequence[Mapping[str, Any] | RLMetaAction] = field(default_factory=tuple)
    queue_weight: float = 1.0
    age_weight: float = 0.1
    ttl_weight: float = 2.0
    scale_up_score: float = 1.0
    scale_down_score: float = 0.15
    scale_step: int = 1
    cooldown_epochs: int = 1

    _cooldowns: Dict[str, int] = field(default_factory=dict, init=False)
    _action_index: int = field(default=0, init=False)
    _strategy_cache: Dict[str, Any] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        """Normalize policy configuration and preflight strategy names."""

        object.__setattr__(
            self,
            "policy",
            str(self.policy).lower().replace("-", "_"),
        )
        if self.policy not in {"threshold", "scripted"}:
            raise ValueError("rl_meta policy must be one of: threshold, scripted")

        normalized = tuple(
            _normalize_strategy_name(strategy)
            for strategy in self.placement_strategies
        )
        if not normalized:
            raise ValueError("rl_meta placement_strategies must contain at least one strategy")
        object.__setattr__(self, "placement_strategies", normalized)

        scripted = tuple(self._coerce_meta_action(action) for action in self.action_sequence)
        object.__setattr__(self, "action_sequence", scripted)
        for strategy in normalized:
            self._strategy_cache[strategy] = self._build_strategy(strategy)

    def step(self, state: ControllerState) -> ControllerAction:
        """Return one scale/replan/strategy action from the meta-policy."""

        features = self._extract_features(state)
        meta_action = self._select_meta_action(state, features)
        scale_actions = self._scale_actions_for_meta_action(state, meta_action)

        replan_requested = (
            bool(meta_action.replan)
            or not state.placement
            or bool(scale_actions)
            or state.epoch % max(1, int(self.replan_every_epochs)) == 0
        )
        placement: Optional[Dict[str, str]] = None
        replan = replan_requested
        replan_deferred = False
        if replan_requested:
            active_services = self._project_active_services(state, scale_actions)
            strategy = self._strategy_cache[meta_action.placement_strategy]
            placement = plan_placement(state, strategy, active_services)
            if not placement:
                if state.placement:
                    scale_actions = []
                    replan = False
                    replan_deferred = True
                    placement = None
                else:
                    raise ValueError(
                        f"rl_meta placement strategy '{meta_action.placement_strategy}' "
                        "could not place active services"
                    )

        return ControllerAction(
            scale=scale_actions,
            placement=placement,
            metadata={
                "controller": "rl_meta",
                "policy": self.policy,
                "meta_action": meta_action.as_dict(),
                "placement_strategy": meta_action.placement_strategy,
                "replan": replan,
                "replan_deferred": replan_deferred,
                "rl_ready": True,
                "reward_logging": True,
                "action_space": self._action_space_metadata(),
                "policy_features": features,
            },
        )

    def _extract_features(self, state: ControllerState) -> Dict[str, Dict[str, float]]:
        """Compute normalized pressure features for every worker pool."""

        features: Dict[str, Dict[str, float]] = {}
        for stage, snapshot in state.worker_pools.items():
            worker_denominator = max(1.0, float(snapshot.active_workers))
            queue_pressure = float(state.queue_depths.get(stage, 0)) / worker_denominator
            age_pressure = float(state.oldest_age_s.get(stage, 0.0)) / worker_denominator
            ttl_pressure = max(0.0, float(state.ttl_pressure.get(stage, 0.0)))
            score = (
                self.queue_weight * queue_pressure
                + self.age_weight * age_pressure
                + self.ttl_weight * ttl_pressure
            )
            features[stage] = {
                "queue_pressure": float(queue_pressure),
                "age_pressure": float(age_pressure),
                "ttl_pressure": float(ttl_pressure),
                "score": float(score),
                "active_workers": float(snapshot.active_workers),
                "ready_workers": float(snapshot.ready_workers),
                "starting_workers": float(snapshot.starting_workers),
            }
        return features

    def _select_meta_action(
        self,
        state: ControllerState,
        features: Mapping[str, Mapping[str, float]],
    ) -> RLMetaAction:
        """Select the discrete meta-action for this epoch."""

        if self.policy == "scripted":
            if not self.action_sequence:
                return RLMetaAction(
                    scale_direction="hold",
                    replan=not state.placement,
                    placement_strategy=self.placement_strategies[0],
                )
            action = self.action_sequence[self._action_index % len(self.action_sequence)]
            self._action_index += 1
            return self._coerce_meta_action(action)

        max_score = max((float(values.get("score", 0.0)) for values in features.values()), default=0.0)
        if max_score >= float(self.scale_up_score):
            return RLMetaAction(
                scale_direction="up",
                replan=True,
                placement_strategy=self._strategy_for_pressure(max_score),
            )
        if max_score <= float(self.scale_down_score):
            return RLMetaAction(
                scale_direction="down",
                replan=True,
                placement_strategy=self.placement_strategies[0],
            )
        return RLMetaAction(
            scale_direction="hold",
            replan=not state.placement,
            placement_strategy=self.placement_strategies[0],
        )

    def _scale_actions_for_meta_action(
        self,
        state: ControllerState,
        meta_action: RLMetaAction,
    ) -> List[ScaleAction]:
        """Translate a scale direction into per-pool replica targets."""

        scale_actions: List[ScaleAction] = []
        step = max(1, int(self.scale_step))
        for stage, snapshot in state.worker_pools.items():
            self._cooldowns[stage] = max(0, self._cooldowns.get(stage, 0) - 1)
            if self._cooldowns[stage] != 0 or meta_action.scale_direction == "hold":
                continue

            target = snapshot.active_workers
            if meta_action.scale_direction == "up":
                target = min(snapshot.max_workers, target + step)
            elif meta_action.scale_direction == "down":
                target = max(snapshot.min_workers, target - step)

            if target != snapshot.active_workers:
                scale_actions.append(ScaleAction(stage=stage, active_workers=target))
                self._cooldowns[stage] = max(0, int(self.cooldown_epochs))
        return scale_actions

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

    def _strategy_for_pressure(self, score: float) -> str:
        """Choose a placement strategy from the configured discrete set."""

        del score
        if "min_latency" in self.placement_strategies:
            return "min_latency"
        return self.placement_strategies[0]

    def _action_space_metadata(self) -> Dict[str, Any]:
        """Return a JSON-friendly description of the discrete action space."""

        return {
            "scale_directions": sorted(_VALID_SCALE_DIRECTIONS),
            "replan": [False, True],
            "placement_strategies": list(self.placement_strategies),
        }

    def _coerce_meta_action(self, action: Mapping[str, Any] | RLMetaAction) -> RLMetaAction:
        """Normalize mapping/scripted actions into ``RLMetaAction`` values."""

        if isinstance(action, RLMetaAction):
            if action.placement_strategy not in self.placement_strategies:
                raise ValueError(
                    f"rl_meta action uses unconfigured placement_strategy: {action.placement_strategy}"
                )
            return action
        meta_action = RLMetaAction(
            scale_direction=str(action.get("scale_direction", "hold")),
            replan=bool(action.get("replan", False)),
            placement_strategy=str(action.get("placement_strategy", self.placement_strategies[0])),
        )
        if meta_action.placement_strategy not in self.placement_strategies:
            raise ValueError(
                f"rl_meta action uses unconfigured placement_strategy: {meta_action.placement_strategy}"
            )
        return meta_action

    def _build_strategy(self, strategy: str):
        """Create the Eclypse-native placement strategy used by a meta-action."""

        return build_placement_strategy(strategy)


def _normalize_strategy_name(strategy: str) -> str:
    """Normalize public placement strategy aliases used by meta-actions."""

    strategy_lower = str(strategy).lower().replace("-", "_")
    if strategy_lower == "bestfit":
        return "best_fit"
    if strategy_lower in {"min_ct", "heft_like"}:
        return "min_latency"
    if strategy_lower == "critical_path":
        return "dependency_aware"
    return strategy_lower
