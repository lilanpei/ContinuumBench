"""Discarded work must count against the SLO, not vanish from it.

The completion-aware SLO rate is ``(B + U) / (A + B + U)``: late completions plus
unfinished work over everything whose deadline has elapsed. Tasks destroyed by a full
buffer used to leave the queue/buffer/inflight sets before that census ran, so they
entered neither the numerator nor the denominator -- a controller that discarded work
scored a *better* SLO rate than one that delivered it late. These tests pin the fix.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from continuum_bench.runner import ContinuumBenchRunner
from continuum_bench.runtime.store_forward import BufferPriority, StoreForwardBuffer

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "split_inference_small.yaml"


def _run(tmp_path: Path, *, buffer_bytes: int, payload_bytes: int) -> dict:
    """Run split inference with a payload/buffer ratio chosen by the caller."""

    cfg = copy.deepcopy(yaml.safe_load(CONFIG.read_text()))
    cfg["epochs"], cfg["drain_epochs"] = 60, 30
    stages = cfg.setdefault("stages", {})
    stages.setdefault("CameraSource", {})["output_size_bytes"] = payload_bytes
    for name in ("CameraSource", "EdgePreprocess", "EdgeInference", "CloudRefineWorker"):
        stages.setdefault(name, {})["buffer"] = {
            "max_size_bytes": buffer_bytes,
            "max_items": 8000,
            "on_overflow": "drop_oldest_low",
        }
    # Thin the link so work backs up into the buffers rather than flowing straight through.
    cfg.setdefault("infrastructure", {})["edge_to_cloud"] = {"latency": 34.0, "bandwidth": 20.0}
    runner = ContinuumBenchRunner(
        config=cfg, controller_name="all_cloud", seed=0, out_root=str(tmp_path)
    )
    return runner.run()["summary"]


def test_discarded_work_is_counted_as_an_slo_violation(tmp_path):
    """A run that drops most of its work must not report a clean SLO record."""

    summary = _run(tmp_path / "drops", buffer_bytes=8 * 1024 * 1024, payload_bytes=10_000_000)

    dropped = summary["store_forward"]["items_dropped_total"]
    assert dropped > 0, "test needs a drop-heavy configuration to be meaningful"
    assert summary["totals"]["completion_ratio"] < 1.0

    # The gaming signature: work destroyed, yet a perfect SLO score.
    assert summary["qos"]["slo_eligible_count"] > 0
    assert summary["qos"]["slo_violation_rate"] > 0.0


def test_dropping_work_does_not_improve_the_slo_rate(tmp_path):
    """Shrinking buffers (so more work is discarded) must not lower the violation rate."""

    roomy = _run(tmp_path / "roomy", buffer_bytes=512 * 1024 * 1024, payload_bytes=10_000_000)
    cramped = _run(tmp_path / "cramped", buffer_bytes=8 * 1024 * 1024, payload_bytes=10_000_000)

    assert (cramped["store_forward"]["items_dropped_total"]
            > roomy["store_forward"]["items_dropped_total"])
    assert cramped["qos"]["slo_violation_rate"] >= roomy["qos"]["slo_violation_rate"]


def test_buffer_retains_discarded_items_for_accounting():
    """Every buffer loss path must surface the item, not just bump a counter."""

    buffer = StoreForwardBuffer(max_size_bytes=100, max_items=2, on_overflow="reject")
    assert buffer.enqueue(data={"id": "a"}, priority=BufferPriority.NORMAL, size_bytes=40, ttl=99)
    assert buffer.enqueue(data={"id": "b"}, priority=BufferPriority.NORMAL, size_bytes=40, ttl=99)
    # Third item exceeds max_items and the reject policy frees nothing.
    assert not buffer.enqueue(data={"id": "c"}, priority=BufferPriority.NORMAL, size_bytes=40, ttl=99)

    assert [item.data["id"] for item in buffer.discarded] == ["c"]
    assert buffer.stats["items_dropped"] == 1
