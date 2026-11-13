"""The anticipatory controller's pre-warm scaling decision.

The controller augments an HPA-style reactive autoscaler (which holds workers up until the queue
clears) with a feed-forward term: knowing the (oracle) burst schedule, it pre-warms the worker pool
BEFORE a window opens so workers finish their startup delay in time for the burst, instead of reacting
after queues build. We test the pure scaling decision `_desired_workers(snapshot, queue_depth, time_s)`
directly (it encodes both the anticipation and the reactive hold), without an Eclypse graph.
"""

from __future__ import annotations

from continuum_bench.controllers import WorkerPoolSnapshot
from continuum_bench.controllers.anticipatory import AnticipatoryContactController


def _snapshot(active: int = 1, total: int = 6, min_workers: int = 1) -> WorkerPoolSnapshot:
    ids = [f"w{i}" for i in range(total)]
    return WorkerPoolSnapshot(
        stage="BulkPath",
        min_workers=min_workers,
        max_workers=total,
        active_workers=active,
        total_workers=total,
        active_worker_ids=ids[:active],
        inactive_worker_ids=ids[active:],
    )


def _controller() -> AnticipatoryContactController:
    # period 60 s, burst window [0,5), pre-warm lead 10 s, drain 15 s; idle band is [20, 50).
    return AnticipatoryContactController(
        period_s=60.0,
        on_duration_s=5.0,
        start_offset_s=0.0,
        lead_s=10.0,
        drain_s=15.0,
        min_replicas=1,
        target_utilization=0.7,
        tolerance=0.1,
    )


def test_prewarms_to_max_before_window_even_with_empty_queue():
    """Anticipation: within lead_s before the next open, scale to max despite zero queue."""
    c = _controller()
    # local_t = 52 in [period-lead=50, period=60): approaching the next burst.
    assert c._desired_workers(_snapshot(active=1), queue_depth=0, time_s=52.0) == 6


def test_warm_during_open_window_with_empty_queue():
    """Workers stay warm across the burst window + drain even before the burst is observed."""
    c = _controller()
    # local_t = 2 in [0, on_duration=5): burst window.
    assert c._desired_workers(_snapshot(active=1), queue_depth=0, time_s=2.0) == 6


def test_scales_down_to_min_mid_idle_when_empty():
    """Cost discipline: in the idle band with no queue, scale to min (timing, not over-provision)."""
    c = _controller()
    # local_t = 30 in [20, 50): idle, empty.
    assert c._desired_workers(_snapshot(active=6), queue_depth=0, time_s=30.0) == 1


def test_holds_workers_until_backlog_clears_even_in_idle_band():
    """HPA-style hold: a lingering backlog in the idle band keeps workers up (no premature scale-down)."""
    c = _controller()
    # local_t = 30 (idle), but 5 tasks still queued on 6 workers -> utilization high -> hold near max.
    assert c._desired_workers(_snapshot(active=6), queue_depth=5, time_s=30.0) == 6


def test_reactive_scale_up_off_schedule():
    """Still a valid autoscaler: off the warm band, a large queue drives an HPA-style scale-up."""
    c = _controller()
    # local_t = 30 (idle), 1 active worker, queue 50 -> ratio >> 1 -> scale to max.
    assert c._desired_workers(_snapshot(active=1), queue_depth=50, time_s=30.0) == 6


def test_prewarm_by_pool_overrides_the_shared_prewarm_target_per_pool():
    ctrl = _controller()
    ctrl.prewarm_workers = 4
    ctrl.prewarm_by_pool = {"Fusion": 2}
    fusion = WorkerPoolSnapshot(stage="Fusion", min_workers=1, max_workers=4, active_workers=1, total_workers=4,
                                active_worker_ids=["f0"], inactive_worker_ids=["f1", "f2", "f3"])
    # t = 55 s is inside the 10 s lead before the burst at 60 s: feed-forward pre-warm applies
    assert ctrl._desired_workers(fusion, queue_depth=0, time_s=55.0) == 2
    assert ctrl._desired_workers(_snapshot(active=1), queue_depth=0, time_s=55.0) == 4
