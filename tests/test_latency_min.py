"""C2 Step 1: summary.latency_s must expose the inherent floor (min latency)."""

from __future__ import annotations

from continuum_bench.metrics.summary import summarize_epochs


def _epoch(latencies_s: list[float]) -> dict:
    # minimal epoch record: completions carry per-task latencies in `latencies_s`
    # (matching how summarize_epochs aggregates the flat `latencies` list).
    return {
        "active_workers": {},
        "completions": {"count": len(latencies_s), "latencies_s": latencies_s},
        "buffers": {"cumulative": {}},
    }


def test_latency_min_is_smallest_completion_latency():
    meta = {"step_seconds": 1.0}
    records = [_epoch([3.0, 5.0]), _epoch([2.0, 7.0])]
    out = summarize_epochs(meta, records)
    assert out["latency_s"]["min"] == 2.0


def test_latency_min_empty_completions_is_zero():
    meta = {"step_seconds": 1.0}
    out = summarize_epochs(meta, [_epoch([])])
    assert out["latency_s"]["min"] == 0.0
