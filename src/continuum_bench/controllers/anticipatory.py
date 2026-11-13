"""Anticipatory autoscaler: pre-warms worker pools ahead of known windows of load.

Reactive autoscalers (HPA/KEDA) only scale *after* a queue builds, so when an intermittent contact
window opens and a buffered burst floods in, the newly activated workers are still inside their
startup delay and miss the burst. This controller adds a feed-forward term: knowing the (oracle)
contact schedule, it pre-warms the worker pool ``lead_s`` before each window opens and holds it warm
across the window plus a drain, then scales back to ``min_replicas`` between bursts. The schedule
is known in advance here; a learned forecaster would approximate it. Comparing with reactive HPA
isolates the value of foresight, and scaling down between windows keeps the comparison fair on cost.
The schedule (``autoscaling.anticipatory``) can describe contact windows or scheduled load bursts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field
from typing import Dict, List, Optional

from continuum_bench.controllers.interfaces import (
    ControllerAction,
    ControllerState,
    ScaleAction,
)
from continuum_bench.controllers.autoscaling_baselines import _PlacementReplanMixin


@dataclass
class AnticipatoryContactController(_PlacementReplanMixin):
    """Feed-forward contact-aware autoscaler with a reactive fallback."""

    period_s: float = 20.0
    on_duration_s: float = 4.0
    start_offset_s: float = 0.0
    lead_s: float = 8.0
    drain_s: float = 4.0
    prewarm_workers: Optional[int] = None  # None -> pre-warm to max_workers
    # Per-pool override of prewarm_workers (case-insensitive pool names), so a two-pool scenario
    # can pre-warm the burst-facing pool without also holding the bulk pool warm.
    prewarm_by_pool: Dict[str, int] = field(default_factory=dict)
    min_replicas: int = 0
    # Reactive (HPA-style) fallback so the controller never abandons a lingering backlog: anticipation
    # is purely additive on top of a competent reactive autoscaler. The win is therefore foresight
    # (catching the burst onset before the startup delay), not a different reactive rule.
    target_utilization: float = 0.7
    tolerance: float = 0.1

    def _reactive_desired(self, snapshot, queue_depth: int) -> int:
        """HPA-style desired replicas: ceil(current * queue/current / target), holding while busy."""

        current = int(snapshot.active_workers)
        q = int(queue_depth)
        target = max(1e-9, float(self.target_utilization))
        if current <= 0:
            return 1 if q > 0 else 0
        ratio = (q / current) / target
        if abs(1.0 - ratio) > max(0.0, float(self.tolerance)):
            return math.ceil(current * ratio)
        return current

    def _desired_workers(self, snapshot, queue_depth: int, time_s: float) -> int:
        """Return the target active worker count: max of the feed-forward and reactive demands."""

        period = max(1e-9, float(self.period_s))
        local_t = (float(time_s) - float(self.start_offset_s)) % period
        in_open_drain = local_t < (float(self.on_duration_s) + float(self.drain_s))
        in_lead = local_t >= (period - float(self.lead_s))
        in_warm = in_open_drain or in_lead

        min_k = max(int(snapshot.min_workers), int(self.min_replicas))
        by_pool = {str(k).lower(): int(v) for k, v in (self.prewarm_by_pool or {}).items()}
        pool_target = by_pool.get(str(snapshot.stage).lower(), self.prewarm_workers)
        prewarm_target = (
            snapshot.max_workers if pool_target is None else min(snapshot.max_workers, int(pool_target))
        )
        feedforward = prewarm_target if in_warm else min_k
        reactive = self._reactive_desired(snapshot, queue_depth)
        desired = max(min_k, int(feedforward), int(reactive))
        return max(int(snapshot.min_workers), min(int(snapshot.max_workers), int(desired)))

    def step(self, state: ControllerState) -> ControllerAction:
        """Return anticipatory scale and placement actions."""

        scale_actions: List[ScaleAction] = []
        for stage, snapshot in state.worker_pools.items():
            desired = self._desired_workers(
                snapshot,
                int(state.queue_depths.get(stage, 0)),
                float(state.time_s),
            )
            if desired != int(snapshot.active_workers):
                scale_actions.append(ScaleAction(stage=stage, active_workers=desired))

        placement, replan, replan_deferred, scale_actions = self._maybe_replan(state, scale_actions)
        return ControllerAction(
            scale=scale_actions,
            placement=placement,
            metadata={
                "controller": "anticipatory",
                "placement_strategy": self.placement_strategy,
                "replan": replan,
                "replan_deferred": replan_deferred,
            },
        )


__all__ = ["AnticipatoryContactController"]
