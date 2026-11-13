"""Constant controller: holds every worker pool at a fixed replica count.

It scales through the same placement-replan path as the autoscaling baselines. It is the
reference that an adaptive controller must beat: a controller that only matches the best
constant allocation has made no decision that changes the outcome.

Names: ``constant:N`` pins N workers in every pool; ``constant:Fusion=2,BulkPath=6`` pins each
pool separately; a bare ``constant`` pins the pool maximum. The target is requested from epoch 0,
so with a non-zero ``startup_delay_s`` the workers become ready after the delay unless
``scaling.pools.<pool>.initial_workers`` already equals N.

Feasibility: when the pinned allocation cannot be placed (for example, when a shared node
budget cannot hold every pool at its maximum), the controller holds the pool at its current
size and reports ``infeasible: true`` in its action metadata instead of aborting the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from continuum_bench.controllers.autoscaling_baselines import _PlacementReplanMixin
from continuum_bench.controllers.interfaces import (
    ControllerAction,
    ControllerState,
    ScaleAction,
)


def parse_constant_target(name: str) -> Union[int, Dict[str, int], None]:
    """Parse the pinned worker count(s) out of a ``constant[:...]`` controller name."""

    if ":" not in name:
        return None
    spec = name.split(":", 1)[1]
    if "=" not in spec:
        return int(spec)
    return {pool.strip(): int(count) for pool, count in (item.split("=", 1) for item in spec.split(","))}


@dataclass
class ConstantController(_PlacementReplanMixin):
    """Pin every worker pool at a constant replica count."""

    active_workers: Union[int, Dict[str, int], None] = None  # None -> the pool maximum

    def _desired_workers(self, snapshot) -> int:
        """Return the pinned target, clamped to the pool's [min, max] range."""

        if isinstance(self.active_workers, dict):
            # Controller names are lower-cased by the runner, so match pools case-insensitively.
            by_pool = {str(pool).lower(): count for pool, count in self.active_workers.items()}
            target = by_pool.get(str(snapshot.stage).lower(), snapshot.max_workers)
        elif self.active_workers is None:
            target = snapshot.max_workers
        else:
            target = self.active_workers
        return max(int(snapshot.min_workers), min(int(snapshot.max_workers), int(target)))

    def step(self, state: ControllerState) -> ControllerAction:
        """Hold each pool at its pinned count; replan placement like the baselines do."""

        scale_actions: List[ScaleAction] = []
        for stage, snapshot in state.worker_pools.items():
            desired = self._desired_workers(snapshot)
            if desired != int(snapshot.active_workers):
                scale_actions.append(ScaleAction(stage=stage, active_workers=desired))

        infeasible = False
        try:
            placement, replan, replan_deferred, scale_actions = self._maybe_replan(state, scale_actions)
        except ValueError:
            # The pinned allocation does not fit the node budget: hold the current pool sizes.
            infeasible = True
            placement, replan, replan_deferred, scale_actions = self._maybe_replan(state, [])
        if scale_actions == [] and replan_deferred:
            infeasible = True
        return ControllerAction(
            scale=scale_actions,
            placement=placement,
            metadata={
                "controller": "constant",
                "target_workers": self.active_workers,
                "placement_strategy": self.placement_strategy,
                "replan": replan,
                "replan_deferred": replan_deferred,
                "infeasible": infeasible,
            },
        )


__all__ = ["ConstantController"]
