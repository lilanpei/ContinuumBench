import csv
import json
import math
import random
from pathlib import Path

import pytest
import continuum_bench.workload.mmpp as mmpp_module

from continuum_bench.metrics import BenchmarkLogger, summarize_epochs
from continuum_bench.workload import MMPPWorkload, PiecewiseRegimeWorkload, build_stream


def _epoch_record() -> dict:
    return {
        "epoch": 3,
        "time_s": 6.0,
        "completions": {
            "generated": 4,
            "generated_by_priority": {"high": 2, "low": 2},
            "slo_eligible_count": 3,
            "slo_eligible_by_priority": {"high": 1, "low": 2},
            "count": 2,
            "latencies_s": [1.0, 3.0],
            "age_at_delivery_s": [1.5, 4.5],
            "delay_components_s": {
                "queue_wait_s": [0.5, 1.5],
                "processing_s": [1.0, 1.0],
                "transport_s": [0.0, 2.0],
                "buffer_wait_s": [0.0, 0.5],
            },
            "deadline_miss_count": 1,
            "slo_violation_count": 2,
            "per_priority": {
                "high": {
                    "completed": 1,
                    "deadline_miss_count": 1,
                    "slo_violation_count": 1,
                },
                "low": {
                    "completed": 1,
                    "deadline_miss_count": 0,
                    "slo_violation_count": 1,
                },
            },
        },
        "traffic": {
            "cross_tier_bytes": 48,
            "by_tier_pair": {
                "space->edge": 32,
                "edge->cloud": 16,
            },
        },
        "decision_time_ms": 5.5,
        "freshness": {"aoi_proxy_s": 2.5},
        "churn": {
            "delta_k": 1.0,
            "migrations": 2.0,
            "startup_delay_s": 4.0,
            "migration_delay_s": 6.0,
        },
        "buffers": {
            "items": 3,
            "bytes": 30,
            "cumulative": {
                "items_buffered": 4,
                "items_transmitted": 2,
                "items_dropped": 1,
                "items_expired": 1,
                "bytes_buffered": 40,
                "bytes_transmitted": 20,
                "bytes_dropped": 10,
                "bytes_expired": 10,
                "priority": {"high": {"items_dropped": 1}},
            },
        },
        "resources": {
            "cpu": {
                "demand": 6.0,
                "capacity": 12.0,
                "utilization": 0.5,
                "overcommit": 0.0,
            },
            "ram": {
                "demand": 5.0,
                "capacity": 4.0,
                "utilization": 1.25,
                "overcommit": 1.0,
            },
            "storage": {
                "demand": 10.0,
                "capacity": 20.0,
                "utilization": 0.5,
                "overcommit": 0.0,
            },
            "nodes": {
                "edge-0": {
                    "cpu": {
                        "demand": 6.0,
                        "capacity": 12.0,
                        "utilization": 0.5,
                        "overcommit": 0.0,
                    },
                    "ram": {
                        "demand": 5.0,
                        "capacity": 4.0,
                        "utilization": 1.25,
                        "overcommit": 1.0,
                    },
                    "storage": {
                        "demand": 10.0,
                        "capacity": 20.0,
                        "utilization": 0.5,
                        "overcommit": 0.0,
                    },
                }
            },
        },
        "energy": {
            "total_j": 20.0,
            "compute_total_j": 18.0,
            "network_total_j": 2.0,
            "by_link_or_tier_pair": {
                "edge-0->cloud-0": {"bytes": 200, "energy_j": 2.0}
            },
            "by_node": {
                "edge-0": {
                    "energy_j": 18.0,
                    "idle_energy_j": 2.0,
                    "dynamic_energy_j": 16.0,
                    "battery_remaining_j": 982.0,
                    "battery_capacity_j": 1000.0,
                }
            },
        },
        "failures": {
            "node_down": ["edge-1"],
            "failed_tasks": 1,
        },
        "controller_reward": {
            "reward": -3.5,
            "components": {
                "latency": -1.0,
                "slo": -1.0,
                "energy": -0.5,
                "reliability": -1.0,
                "churn": 0.0,
            },
        },
    }


def test_build_stream_supports_piecewise_and_mmpp_generators() -> None:
    piecewise = build_stream(
        {
            "generator": "piecewise",
            "segments": [{"epochs": 1, "rate": 0.0}],
        },
        seed=7,
    )
    mmpp = build_stream({"generator": "mmpp"}, seed=7)

    assert isinstance(piecewise, PiecewiseRegimeWorkload)
    assert isinstance(mmpp, MMPPWorkload)

    with pytest.raises(ValueError, match="Unsupported workload generator"):
        build_stream({"generator": "unknown"}, seed=7)


def test_piecewise_workload_can_repeat_regime_pattern_for_long_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mmpp_module, "_sample_poisson", lambda rate, rng: int(rate))
    workload = build_stream(
        {
            "generator": "piecewise",
            "repeat": True,
            "segments": [
                {"epochs": 2, "rate": 1.0},
                {"epochs": 1, "rate": 4.0},
            ],
        },
        seed=7,
    )

    assert [workload.sample(epoch) for epoch in range(7)] == [1, 1, 4, 1, 1, 4, 1]


def test_benchmark_logger_writes_epoch_and_summary_files(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    metadata = {"seed": 7, "step_seconds": 2.0}
    record = _epoch_record()
    summary = {"totals": {"epochs": 1}}

    with BenchmarkLogger(run_dir, metadata=metadata) as logger:
        logger.log_epoch(record)
        logger.write_summary(summary)

    assert json.loads((run_dir / "metadata.json").read_text(encoding="utf-8")) == metadata
    assert json.loads((run_dir / "summary.json").read_text(encoding="utf-8")) == summary
    assert [
        json.loads(line)
        for line in (run_dir / "epochs.jsonl").read_text(encoding="utf-8").splitlines()
    ] == [record]

    with (run_dir / "epochs.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "epoch": "3",
            "time_s": "6.0",
            "generated": "4",
            "completed": "2",
            "deadline_miss_count": "1",
            "slo_violation_count": "2",
            "decision_time_ms": "5.5",
            "buffer_items": "3",
            "buffer_bytes": "30",
            "cross_tier_bytes": "48",
            "delta_k": "1.0",
            "migrations": "2.0",
        }
    ]


def test_summarize_epochs_aggregates_basic_metrics() -> None:
    metadata = {"scenario": "demo", "step_seconds": 2.0}

    summary = summarize_epochs(metadata, [_epoch_record()])

    assert summary["metadata"] == metadata
    assert summary["totals"] == {
        "epochs": 1,
        "duration_s": 2.0,
        "generated_tasks": 4,
        "completed_tasks": 2,
        "completion_ratio": 0.5,
        "pending_tasks": 0,
        "pending_rate": 0.0,
        "resolved_rate": 1.0,
        "throughput_tasks_per_s": 1.0,
    }
    assert summary["latency_s"]["count"] == 2
    assert summary["latency_s"]["mean"] == 2.0
    assert summary["freshness"]["age_at_delivery_p50"] == 3.0
    assert summary["delay_breakdown_s"]["queue_wait_s"]["mean"] == 1.0
    assert summary["delay_breakdown_s"]["transport_s"]["p95"] == pytest.approx(1.9)
    assert summary["delay_breakdown_s"]["startup_delay_s"]["mean"] == 4.0
    assert summary["delay_breakdown_s"]["migration_delay_s"]["p95"] == 6.0
    assert summary["freshness"]["aoi_proxy_mean"] == 2.5
    assert summary["store_forward"]["buffered_items_peak"] == 3.0
    assert summary["store_forward"]["drop_rate"] == 0.25
    assert summary["store_forward"]["per_priority"] == {"high": {"items_dropped": 1}}
    assert summary["stability"]["scaling_delta_abs_total"] == 1.0
    assert summary["controller_overhead_ms"]["mean"] == 5.5


def test_summarize_epochs_reports_qos_traffic_and_completion_ratio() -> None:
    metadata = {"scenario": "demo", "step_seconds": 2.0}

    summary = summarize_epochs(metadata, [_epoch_record()])

    assert summary["totals"]["generated_tasks"] == 4
    assert summary["totals"]["completion_ratio"] == 0.5
    assert summary["qos"]["deadline_miss_count"] == 1
    assert summary["qos"]["slo_violation_count"] == 2
    assert summary["qos"]["slo_eligible_count"] == 3
    assert summary["qos"]["deadline_miss_rate"] == 0.5
    assert summary["qos"]["slo_violation_rate"] == pytest.approx(2.0 / 3.0)
    assert summary["qos"]["per_priority"] == {
        "high": {
            "generated": 2,
            "completed": 1,
            "deadline_miss_count": 1,
            "slo_violation_count": 1,
            "slo_eligible_count": 1,
        },
        "low": {
            "generated": 2,
            "completed": 1,
            "deadline_miss_count": 0,
            "slo_violation_count": 1,
            "slo_eligible_count": 2,
        },
    }
    assert summary["traffic"] == {
        "cross_tier_bytes_total": 48,
        "by_tier_pair": {
            "space->edge": 32,
            "edge->cloud": 16,
        },
    }


def test_summarize_epochs_reports_sota_resource_energy_and_reliability_metrics() -> None:
    metadata = {"scenario": "demo", "step_seconds": 2.0}

    summary = summarize_epochs(metadata, [_epoch_record()])

    assert summary["resource_utilization"]["cpu_mean"] == 0.5
    assert summary["resource_utilization"]["cpu_peak"] == 0.5
    assert summary["resource_utilization"]["cpu_overcommit_mean"] == 0.0
    assert summary["resource_utilization"]["ram_mean"] == 1.25
    assert summary["resource_utilization"]["ram_overcommit_mean"] == 1.0
    assert summary["resource_utilization"]["storage_mean"] == 0.5
    assert summary["energy"]["total_j"] == 20.0
    assert summary["energy"]["compute_total_j"] == 18.0
    assert summary["energy"]["network_total_j"] == 2.0
    assert summary["energy"]["mean_power_w"] == 10.0
    assert summary["energy"]["battery_remaining_j_by_node"] == {"edge-0": 982.0}
    assert summary["reliability"] == {
        "generated_tasks": 4,
        "completed_tasks": 2,
        "pending_tasks": 0,
        "resolved_tasks": 4,
        "failed_tasks": 3,
        "buffer_failed_tasks": 2,
        "failure_event_failed_tasks": 1,
        "success_rate": 0.5,
        "resolved_rate": 1.0,
        "pending_rate": 0.0,
        "failure_rate": 0.75,
        "drop_rate": 0.25,
        "expire_rate": 0.25,
    }
    assert summary["failures"] == {
        "node_down_events": 1,
        "failed_tasks": 1,
    }
    assert summary["controller_reward"]["mean"] == -3.5


def test_summarize_epochs_reports_horizon_pending_separately_from_failures() -> None:
    metadata = {"scenario": "demo", "step_seconds": 1.0}
    record = _epoch_record()
    record["completions"] = {
        **record["completions"],
        "generated": 8,
        "count": 3,
        "latencies_s": [1.0, 2.0, 3.0],
        "age_at_delivery_s": [1.0, 2.0, 3.0],
        "deadline_miss_count": 0,
        "slo_violation_count": 0,
        "per_priority": {},
    }
    record["buffers"]["cumulative"] = {
        **record["buffers"]["cumulative"],
        "items_buffered": 5,
        "items_transmitted": 0,
        "items_dropped": 1,
        "items_expired": 1,
    }
    record["failures"] = {"node_down": [], "failed_tasks": 1}

    summary = summarize_epochs(metadata, [record])

    assert summary["reliability"]["completed_tasks"] == 3
    assert summary["reliability"]["failed_tasks"] == 3
    assert summary["reliability"]["pending_tasks"] == 2
    assert summary["reliability"]["failure_rate"] == pytest.approx(3.0 / 8.0)
    assert summary["reliability"]["pending_rate"] == pytest.approx(2.0 / 8.0)
    assert summary["reliability"]["resolved_rate"] == pytest.approx(6.0 / 8.0)


def test_sample_poisson_large_rates_remain_rate_sensitive() -> None:
    class ConstantRandom:
        def random(self) -> float:
            return math.exp(-1.0)

    lower = mmpp_module._sample_poisson(800.0, ConstantRandom())
    higher = mmpp_module._sample_poisson(1600.0, ConstantRandom())

    assert higher > lower + 500


def test_summarize_epochs_uses_absolute_scaling_delta_values() -> None:
    metadata = {"step_seconds": 1.0}
    records = [
        {
            "completions": {},
            "churn": {"delta_k": -2.0, "migrations": 0.0},
            "buffers": {"items": 0, "bytes": 0, "cumulative": {}},
        },
        {
            "completions": {},
            "churn": {"delta_k": 1.5, "migrations": 0.0},
            "buffers": {"items": 0, "bytes": 0, "cumulative": {}},
        },
    ]

    summary = summarize_epochs(metadata, records)

    assert summary["stability"]["scaling_delta_abs_total"] == 3.5
    assert summary["stability"]["scaling_delta_abs_mean"] == 1.75


def test_summarize_epochs_reports_startup_and_migration_overhead_rollups() -> None:
    metadata = {"step_seconds": 1.0}
    records = [
        {
            "completions": {},
            "churn": {
                "delta_k": 0.0,
                "migrations": 1.0,
                "startup_delay_s": 4.0,
                "migration_delay_s": 2.0,
            },
            "buffers": {"items": 0, "bytes": 0, "cumulative": {}},
        },
        {
            "completions": {},
            "churn": {
                "delta_k": 0.0,
                "migrations": 0.0,
                "startup_delay_s": 0.0,
                "migration_delay_s": 0.0,
            },
            "buffers": {"items": 0, "bytes": 0, "cumulative": {}},
        },
    ]

    summary = summarize_epochs(metadata, records)

    assert summary["stability"]["startup_delay_s_total"] == 4.0
    assert summary["stability"]["startup_delay_s_mean"] == 2.0
    assert summary["stability"]["migration_delay_s_total"] == 2.0
    assert summary["stability"]["migration_delay_s_mean"] == 1.0


def test_mmpp_uses_initial_state_for_first_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mmpp_module, "_sample_poisson", lambda rate, rng: int(rate))

    workload = MMPPWorkload(
        low_rate=2.0,
        high_rate=9.0,
        p_low_to_high=1.0,
        p_high_to_low=0.0,
        seed=7,
        initial_state="low",
    )

    assert workload.sample(0) == 2
    assert workload.sample(1) == 9
