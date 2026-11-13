"""TDD for the single in-sim provisioned-worker-second cost (D4)."""

from __future__ import annotations

from continuum_bench.metrics.summary import summarize_epochs


def _epoch(active: dict, ready: dict | None = None) -> dict:
    # minimal epoch record shape summarize_epochs reads; extend with required keys per the real schema.
    return {"active_workers": active, "ready_workers": active if ready is None else ready,
            "completions": {}, "buffers": {"cumulative": {}}}


def test_cost_sums_active_worker_seconds_over_all_pools():
    meta = {"step_seconds": 2.0, "cost_flat_price_per_worker_second": 1.0}
    records = [_epoch({"A": 2, "B": 1}), _epoch({"A": 3, "B": 1})]
    out = summarize_epochs(meta, records)
    # total = sum_epoch( sum_stage active ) * step * price = (3 + 4) * 2 * 1 = 14
    assert out["cost"]["total_provisioned_worker_seconds"] == 14.0
    # mean active workers (analyzer-compatible) = mean(3, 4) = 3.5
    assert out["cost"]["mean_active_workers"] == 3.5


def test_cost_respects_flat_price():
    meta = {"step_seconds": 1.0, "cost_flat_price_per_worker_second": 0.5}
    out = summarize_epochs(meta, [_epoch({"A": 2})])
    assert out["cost"]["total_provisioned_worker_seconds"] == 1.0   # 2 * 1 * 0.5


def test_empty_records_has_zero_cost_block():
    out = summarize_epochs({"step_seconds": 1.0}, [])
    assert out["cost"]["total_provisioned_worker_seconds"] == 0.0
    assert out["cost"]["mean_active_workers"] == 0.0


def test_cost_bills_provisioned_workers_including_warming_not_just_ready():
    # active (provisioned, incl. warming) > ready: cost must track active, not ready.
    meta = {"step_seconds": 1.0, "cost_flat_price_per_worker_second": 1.0}
    out = summarize_epochs(meta, [_epoch(active={"A": 3}, ready={"A": 1})])
    # Billed on active=3 (provisioned), NOT ready=1.
    assert out["cost"]["total_provisioned_worker_seconds"] == 3.0
    assert out["cost"]["mean_active_workers"] == 3.0


def test_empty_branch_echoes_configured_flat_price():
    out = summarize_epochs({"step_seconds": 1.0, "cost_flat_price_per_worker_second": 0.5}, [])
    # Empty branch must echo the configured price, matching the populated branch.
    assert out["cost"]["flat_price_per_worker_second"] == 0.5


def test_tier_priced_cost_uses_worker_placement_and_tier_prices():
    """Worker-seconds are priced by the tier that hosts each active worker."""

    meta = {
        "step_seconds": 1.0,
        "cost_flat_price_per_worker_second": 1.0,
        "cost_price_per_tier": {"edge": 4.0, "cloud": 0.5},
        "node_tiers": {"Edge_0": "edge", "Cloud_0": "cloud", "IoT_0": "iot"},
        "worker_pools": {"BulkPath": ["w0", "w1"]},
    }
    rec = _epoch({"BulkPath": 2})
    rec["placement"] = {"w0": "Edge_0", "w1": "Cloud_0", "Sink": "IoT_0"}  # Sink is not a worker
    out = summarize_epochs(meta, [rec, rec])
    assert out["cost"]["worker_seconds_by_tier"] == {"edge": 2.0, "cloud": 2.0}
    assert out["cost"]["total_tier_priced_worker_seconds"] == 2 * 4.0 + 2 * 0.5
    assert out["cost"]["total_provisioned_worker_seconds"] == 4.0  # flat accounting unchanged


def test_tier_priced_cost_is_empty_without_run_metadata():
    out = summarize_epochs({"step_seconds": 1.0}, [_epoch({"A": 2})])
    assert out["cost"]["worker_seconds_by_tier"] == {}
    assert out["cost"]["total_tier_priced_worker_seconds"] == 0.0
