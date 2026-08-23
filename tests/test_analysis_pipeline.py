from __future__ import annotations

import csv
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_module(relative_path: str, module_name: str):
    module_path = ROOT / relative_path
    assert module_path.exists(), f"Missing analysis module: {relative_path}"

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_summary(
    run_dir: Path,
    *,
    scenario: str,
    controller: str,
    seed: int,
    throughput: float,
    latency_p95: float,
    slo_violation_rate: float,
    aoi_proxy_mean: float,
    drop_rate: float,
    migrations_total: float,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    completed_tasks = int(throughput * 10.0)
    reliability_success_rate = min(1.0, completed_tasks / 120.0)
    failed_tasks = 1 + int(round(drop_rate * 12))
    pending_tasks = max(0, 120 - completed_tasks - failed_tasks)
    reliability_failure_rate = failed_tasks / 120.0

    summary = {
        "metadata": {
            "scenario": scenario,
            "controller": controller,
            "seed": seed,
            "step_seconds": 1.0,
            "run_id": run_dir.name,
            "run_dir": str(run_dir),
            "realism": {
                "declared_tier": "T0",
                "detected_tier": "T0",
                "label": "base_deterministic",
            },
        },
        "totals": {
            "epochs": 10,
            "duration_s": 10.0,
            "generated_tasks": 120,
            "completed_tasks": completed_tasks,
            "completion_ratio": throughput / 12.0,
            "pending_tasks": pending_tasks,
            "pending_rate": pending_tasks / 120.0,
            "resolved_rate": (completed_tasks + failed_tasks) / 120.0,
            "throughput_tasks_per_s": throughput,
        },
        "latency_s": {
            "count": 120,
            "p50": round(latency_p95 * 0.5, 6),
            "p95": latency_p95,
            "p99": round(latency_p95 * 1.2, 6),
            "mean": round(latency_p95 * 0.6, 6),
        },
        "qos": {
            "deadline_miss_count": 3,
            "slo_violation_count": 5,
            "slo_eligible_count": 120,
            "deadline_miss_rate": 0.025,
            "slo_violation_rate": slo_violation_rate,
            "per_priority": {},
        },
        "traffic": {
            "cross_tier_bytes_total": 4096,
            "by_tier_pair": {"iot->edge": 2048, "edge->cloud": 2048},
        },
        "freshness": {
            "age_at_delivery_p50": round(aoi_proxy_mean * 0.5, 6),
            "age_at_delivery_p95": round(aoi_proxy_mean * 1.5, 6),
            "age_at_delivery_p99": round(aoi_proxy_mean * 1.8, 6),
            "aoi_proxy_mean": aoi_proxy_mean,
            "aoi_proxy_p95": round(aoi_proxy_mean * 1.4, 6),
        },
        "resource_utilization": {
            "cpu_mean": 0.625,
            "cpu_peak": 0.75,
            "cpu_overcommit_mean": 0.1,
            "cpu_overcommit_peak": 0.2,
            "ram_mean": 0.5,
            "ram_peak": 0.6,
            "ram_overcommit_mean": 0.0,
            "ram_overcommit_peak": 0.0,
            "storage_mean": 0.25,
            "storage_peak": 0.3,
            "storage_overcommit_mean": 0.0,
            "storage_overcommit_peak": 0.0,
        },
        "energy": {
            "total_j": 42.0,
            "compute_total_j": 40.0,
            "network_total_j": 2.0,
            "mean_power_w": 4.2,
            "battery_remaining_j_by_node": {"edge-0": 958.0},
        },
        "reliability": {
            "generated_tasks": 120,
            "completed_tasks": completed_tasks,
            "pending_tasks": pending_tasks,
            "resolved_tasks": completed_tasks + failed_tasks,
            "failed_tasks": failed_tasks,
            "buffer_failed_tasks": int(round(drop_rate * 12)),
            "failure_event_failed_tasks": 1,
            "success_rate": reliability_success_rate,
            "resolved_rate": (completed_tasks + failed_tasks) / 120.0,
            "pending_rate": pending_tasks / 120.0,
            "failure_rate": reliability_failure_rate,
            "drop_rate": drop_rate,
            "expire_rate": 0.0,
        },
        "failures": {
            "node_down_events": 1,
            "failed_tasks": 1,
        },
        "controller_reward": {
            "mean": -0.5,
            "min": -1.0,
            "max": 0.0,
        },
        "store_forward": {
            "buffered_items_peak": 8.0,
            "buffered_bytes_peak": 800.0,
            "items_buffered_total": 12,
            "items_transmitted_total": 10,
            "items_dropped_total": int(round(drop_rate * 12)),
            "items_expired_total": 0,
            "bytes_buffered_total": 1200,
            "bytes_transmitted_total": 1000,
            "bytes_dropped_total": int(round(drop_rate * 1200)),
            "bytes_expired_total": 0,
            "drop_rate": drop_rate,
            "expire_rate": 0.0,
            "per_priority": {},
        },
        "contact": {
            "active_link_ratio_mean": 0.5,
            "active_links_mean": 1.0,
            "total_links_mean": 2.0,
            "closed_link_epochs": 5,
        },
        "stability": {
            "scaling_delta_abs_total": migrations_total + 1.0,
            "scaling_delta_abs_mean": (migrations_total + 1.0) / 10.0,
            "migrations_total": migrations_total,
            "migrations_per_epoch": migrations_total / 10.0,
            "startup_delay_s_total": migrations_total * 0.4,
            "startup_delay_s_mean": migrations_total * 0.04,
            "migration_delay_s_total": migrations_total * 0.8,
            "migration_delay_s_mean": migrations_total * 0.08,
        },
        "controller_overhead_ms": {
            "p50": 1.5,
            "p95": 2.5,
            "mean": 2.0,
        },
        "delay_breakdown_s": {
            "queue_wait_s": {"mean": 3.0, "p95": 4.0},
            "processing_s": {"mean": 2.0, "p95": 2.0},
            "transport_s": {"mean": 1.5, "p95": 2.5},
            "buffer_wait_s": {"mean": 0.5, "p95": 1.0},
            "startup_delay_s": {"mean": 0.0, "p95": 0.0},
            "migration_delay_s": {"mean": 0.0, "p95": 0.0},
        },
    }

    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def _write_provenance_config(run_dir: Path, payload: dict) -> None:
    provenance_dir = run_dir / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)
    (provenance_dir / "config.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )


def test_multiple_run_summaries_aggregate_into_tidy_table(tmp_path: Path) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs")
    sweep_dir = tmp_path / "runs"
    output_dir = tmp_path / "analysis_out"

    _write_summary(
        sweep_dir / "run_a",
        scenario="eo_dual_stream",
        controller="no_scaling",
        seed=1,
        throughput=10.0,
        latency_p95=2.0,
        slo_violation_rate=0.10,
        aoi_proxy_mean=1.5,
        drop_rate=0.0,
        migrations_total=1.0,
    )
    _write_summary(
        sweep_dir / "run_b",
        scenario="eo_dual_stream",
        controller="separated_loops",
        seed=2,
        throughput=12.0,
        latency_p95=1.6,
        slo_violation_rate=0.05,
        aoi_proxy_mean=1.2,
        drop_rate=0.0,
        migrations_total=3.0,
    )

    result = aggregate_runs.aggregate_sweep(sweep_dir, output_dir=output_dir)

    assert Path(result["aggregate_csv"]).exists()
    with Path(result["aggregate_csv"]).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["run_id"] for row in rows] == ["run_a", "run_b"]
    assert rows[0] == {
        "scenario": "eo_dual_stream",
        "controller": "no_scaling",
        "evaluation_regime": "F0",
        "evaluation_label": "deterministic_synthetic",
        "energy_overlay": "False",
        "failure_overlay": "False",
        "contact_mobility_overlay": "False",
        "calibration_overlay": "False",
        "workload_arrival_rate_mean": "",
        "failure_mode": "",
        "node_failure_probability": "",
        "contact_profile": "",
        "realism_tier": "T0",
        "realism_label": "base_deterministic",
        "seed": "1",
        "run_id": "run_a",
        "run_dir": str(sweep_dir / "run_a"),
        "epochs": "10",
        "duration_s": "10.0",
        "generated_tasks": "120",
        "completed_tasks": "100",
        "completion_ratio": "0.8333333333333334",
        "pending_tasks": "19",
        "pending_rate": "0.15833333333333333",
        "resolved_rate": "0.8416666666666667",
        "throughput_tasks_per_s": "10.0",
        "latency_p50_s": "1.0",
        "latency_p95_s": "2.0",
        "latency_p99_s": "2.4",
        "latency_mean_s": "1.2",
        "delay_queue_wait_mean_s": "3.0",
        "delay_processing_mean_s": "2.0",
        "delay_transport_mean_s": "1.5",
        "delay_buffer_wait_mean_s": "0.5",
        "delay_startup_mean_s": "0.0",
        "delay_migration_mean_s": "0.0",
        "deadline_miss_rate": "0.025",
        "slo_violation_rate": "0.1",
        "age_at_delivery_p95_s": "2.25",
        "aoi_proxy_mean_s": "1.5",
        "drop_rate": "0.0",
        "expire_rate": "0.0",
        "buffered_items_peak": "8.0",
        "active_link_ratio_mean": "0.5",
        "closed_link_epochs": "5",
        "migrations_total": "1.0",
        "scaling_delta_abs_total": "2.0",
        "controller_overhead_p95_ms": "2.5",
        "cpu_utilization_mean": "0.625",
        "cpu_utilization_peak": "0.75",
        "cpu_overcommit_mean": "0.1",
        "cpu_overcommit_peak": "0.2",
        "ram_utilization_mean": "0.5",
        "storage_utilization_mean": "0.25",
        "energy_total_j": "42.0",
        "network_energy_total_j": "2.0",
        "mean_power_w": "4.2",
        "reliability_success_rate": "0.8333333333333334",
        "reliability_failure_rate": "0.008333333333333333",
        "reliability_pending_rate": "0.15833333333333333",
        "reliability_resolved_rate": "0.8416666666666667",
        "failure_event_failed_tasks": "1",
        "controller_reward_mean": "-0.5",
    }
    assert rows[1]["controller"] == "separated_loops"
    assert rows[1]["evaluation_regime"] == "F0"
    assert rows[1]["realism_tier"] == "T0"
    assert rows[1]["throughput_tasks_per_s"] == "12.0"
    assert rows[1]["latency_p95_s"] == "1.6"


def test_grouped_summaries_compute_stable_statistics_for_tables(tmp_path: Path) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs_grouped")
    sweep_dir = tmp_path / "runs"
    output_dir = tmp_path / "analysis_out"

    _write_summary(
        sweep_dir / "bf_seed_1",
        scenario="eo_dual_stream",
        controller="no_scaling",
        seed=1,
        throughput=10.0,
        latency_p95=2.0,
        slo_violation_rate=0.20,
        aoi_proxy_mean=1.5,
        drop_rate=0.10,
        migrations_total=1.0,
    )
    _write_summary(
        sweep_dir / "bf_seed_2",
        scenario="eo_dual_stream",
        controller="no_scaling",
        seed=2,
        throughput=12.0,
        latency_p95=2.5,
        slo_violation_rate=0.10,
        aoi_proxy_mean=1.7,
        drop_rate=0.20,
        migrations_total=3.0,
    )
    _write_summary(
        sweep_dir / "sl_seed_1",
        scenario="eo_dual_stream",
        controller="separated_loops",
        seed=1,
        throughput=14.0,
        latency_p95=1.5,
        slo_violation_rate=0.05,
        aoi_proxy_mean=1.1,
        drop_rate=0.00,
        migrations_total=4.0,
    )

    result = aggregate_runs.aggregate_sweep(sweep_dir, output_dir=output_dir)
    grouped = json.loads(Path(result["grouped_json"]).read_text(encoding="utf-8"))

    assert grouped["group_by"] == ["scenario", "controller"]
    assert [(group["controller"], group["runs"]) for group in grouped["groups"]] == [
        ("no_scaling", 2),
        ("separated_loops", 1),
    ]
    no_scaling_metrics = grouped["groups"][0]["metrics"]
    assert no_scaling_metrics["throughput_tasks_per_s"] == {
        "mean": 11.0,
        "min": 10.0,
        "max": 12.0,
    }
    assert no_scaling_metrics["completion_ratio"] == {
        "mean": 0.9166666667,
        "min": 0.8333333333,
        "max": 1.0,
    }
    assert no_scaling_metrics["migrations_total"] == {
        "mean": 2.0,
        "min": 1.0,
        "max": 3.0,
    }
    assert no_scaling_metrics["delay_queue_wait_mean_s"] == {
        "mean": 3.0,
        "min": 3.0,
        "max": 3.0,
    }
    assert no_scaling_metrics["delay_transport_mean_s"] == {
        "mean": 1.5,
        "min": 1.5,
        "max": 1.5,
    }

    separated_metrics = grouped["groups"][1]["metrics"]
    assert separated_metrics["throughput_tasks_per_s"] == {
        "mean": 14.0,
        "min": 14.0,
        "max": 14.0,
    }
    assert separated_metrics["delay_buffer_wait_mean_s"] == {
        "mean": 0.5,
        "min": 0.5,
        "max": 0.5,
    }


def test_tables_rank_with_completion_aware_guard(tmp_path: Path) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs_for_tables")
    tables = _load_module("analysis/tables.py", "tables")
    sweep_dir = tmp_path / "runs"
    aggregate_dir = tmp_path / "aggregate_out"
    tables_dir = tmp_path / "tables_out"

    _write_summary(
        sweep_dir / "fast_but_incomplete",
        scenario="eo_dual_stream",
        controller="hpa",
        seed=0,
        throughput=0.6,
        latency_p95=0.5,
        slo_violation_rate=0.9,
        aoi_proxy_mean=1.0,
        drop_rate=0.8,
        migrations_total=0.0,
    )
    _write_summary(
        sweep_dir / "balanced",
        scenario="eo_dual_stream",
        controller="learned_stub",
        seed=0,
        throughput=8.0,
        latency_p95=2.0,
        slo_violation_rate=0.1,
        aoi_proxy_mean=1.0,
        drop_rate=0.0,
        migrations_total=0.0,
    )

    aggregate = aggregate_runs.aggregate_sweep(sweep_dir, output_dir=aggregate_dir)
    outputs = tables.build_tables(
        aggregate["aggregate_csv"],
        output_dir=tables_dir,
        min_completion_for_latency_rank=0.1,
    )

    with Path(outputs["ranking_csv"]).open(encoding="utf-8", newline="") as handle:
        rankings = list(csv.DictReader(handle))

    hpa = next(row for row in rankings if row["controller"] == "hpa")
    learned = next(row for row in rankings if row["controller"] == "learned_stub")

    assert {row["evaluation_regime"] for row in rankings} == {"F0"}
    assert hpa["valid_for_latency_rank"] == "False"
    assert hpa["latency_rank_if_valid"] == ""
    assert learned["valid_for_latency_rank"] == "True"
    assert learned["latency_rank_if_valid"] == "1"
    assert learned["score_rank"] == "1"


def test_composite_score_rank_keeps_low_completion_rows_below_valid_rows(tmp_path: Path) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs_for_guarded_score")
    tables = _load_module("analysis/tables.py", "tables_guarded_score")
    sweep_dir = tmp_path / "runs"
    aggregate_dir = tmp_path / "aggregate_out"
    tables_dir = tmp_path / "tables_out"

    _write_summary(
        sweep_dir / "failed_fast",
        scenario="driving_v2x",
        controller="best_fit",
        seed=0,
        throughput=0.0,
        latency_p95=0.0,
        slo_violation_rate=1.0,
        aoi_proxy_mean=0.0,
        drop_rate=8.0,
        migrations_total=0.0,
    )
    _write_summary(
        sweep_dir / "slow_but_delivered",
        scenario="driving_v2x",
        controller="learned_stub",
        seed=0,
        throughput=6.0,
        latency_p95=100.0,
        slo_violation_rate=0.95,
        aoi_proxy_mean=1.0,
        drop_rate=0.0,
        migrations_total=0.0,
    )

    aggregate = aggregate_runs.aggregate_sweep(sweep_dir, output_dir=aggregate_dir)
    outputs = tables.build_tables(
        aggregate["aggregate_csv"],
        output_dir=tables_dir,
        min_completion_for_latency_rank=0.1,
    )

    with Path(outputs["ranking_csv"]).open(encoding="utf-8", newline="") as handle:
        rankings = list(csv.DictReader(handle))

    failed_fast = next(row for row in rankings if row["controller"] == "best_fit")
    delivered = next(row for row in rankings if row["controller"] == "learned_stub")

    assert failed_fast["valid_for_latency_rank"] == "False"
    assert delivered["valid_for_latency_rank"] == "True"
    assert delivered["score_rank"] == "1"
    assert failed_fast["score_rank"] == "2"


def test_score_sensitivity_reports_family_level_margin(tmp_path: Path) -> None:
    score_sensitivity = _load_module(
        "analysis/score_sensitivity.py",
        "score_sensitivity_for_guarded_score",
    )
    grouped_csv = tmp_path / "grouped_strict.csv"
    fields = [
        "scenario",
        "evaluation_regime",
        "controller",
        "valid_for_latency_rank",
        "completion_ratio",
        "pending_rate",
        "slo_violation_rate",
        "latency_p95_s",
        "migrations_total",
    ]
    with grouped_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for scenario in score_sensitivity.SCENARIOS:
            for regime in score_sensitivity.REGIMES:
                writer.writerows(
                    [
                        {
                            "scenario": scenario,
                            "evaluation_regime": regime,
                            "controller": "hpa",
                            "valid_for_latency_rank": "True",
                            "completion_ratio": "0.8",
                            "pending_rate": "0.1",
                            "slo_violation_rate": "0.1",
                            "latency_p95_s": "20",
                            "migrations_total": "1",
                        },
                        {
                            "scenario": scenario,
                            "evaluation_regime": regime,
                            "controller": "best_fit",
                            "valid_for_latency_rank": "True",
                            "completion_ratio": "0.6",
                            "pending_rate": "0.2",
                            "slo_violation_rate": "0.3",
                            "latency_p95_s": "30",
                            "migrations_total": "0",
                        },
                    ]
                )

    summary = score_sensitivity.score_sensitivity(grouped_csv)

    assert summary["weight_grid_size"] == 81
    assert summary["total_contexts"] == 8
    assert summary["contexts_with_controller_choice_changes"] == 0
    assert summary["min_completion_delta_scale_minus_placement"] == pytest.approx(0.2)
    assert summary["min_slo_reduction_placement_minus_scale"] == pytest.approx(0.2)


def test_tables_keep_load_and_failure_sensitivity_settings_separate(
    tmp_path: Path,
) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs_for_sensitivity_tables")
    tables = _load_module("analysis/tables.py", "tables_sensitivity")
    sweep_dir = tmp_path / "runs"
    aggregate_dir = tmp_path / "aggregate_out"
    tables_dir = tmp_path / "tables_out"

    for name, rate, failure_probability in [
        ("low_failure_none", 1.0, 0.0),
        ("high_failure_none", 3.0, 0.0),
        ("base_failure_mild", 2.0, 0.03),
        ("base_failure_stress", 2.0, 0.08),
    ]:
        run_dir = sweep_dir / name
        _write_summary(
            run_dir,
            scenario="eo_dual_stream",
            controller="hpa",
            seed=0,
            throughput=10.0,
            latency_p95=2.0,
            slo_violation_rate=0.10,
            aoi_proxy_mean=1.0,
            drop_rate=0.0,
            migrations_total=0.0,
        )
        _write_provenance_config(
            run_dir,
            {
                "workload": {
                    "streams": {
                        "SpaceSource": {
                            "generator": "piecewise",
                            "segments": [{"epochs": 10, "rate": rate}],
                        }
                    }
                },
                "reliability": {
                    "failures": {
                        "mode": "disabled" if failure_probability == 0.0 else "bernoulli",
                        "node_failure_probability": failure_probability,
                    }
                },
                "infrastructure": {
                    "contact_schedule": {
                        "mode": "trace",
                        "path": "src/continuum_bench/calibration/contact_traces/eo_space_ground_stress.csv",
                    }
                },
            },
        )

    aggregate = aggregate_runs.aggregate_sweep(sweep_dir, output_dir=aggregate_dir)
    with Path(aggregate["aggregate_csv"]).open(encoding="utf-8", newline="") as handle:
        aggregate_rows = list(csv.DictReader(handle))

    assert {row["workload_arrival_rate_mean"] for row in aggregate_rows} == {"1", "2", "3"}
    assert {row["node_failure_probability"] for row in aggregate_rows} == {"0", "0.03", "0.08"}
    assert {row["contact_profile"] for row in aggregate_rows} == {"stress"}

    outputs = tables.build_tables(aggregate["aggregate_csv"], output_dir=tables_dir)
    with Path(outputs["grouped_csv"]).open(encoding="utf-8", newline="") as handle:
        grouped = list(csv.DictReader(handle))

    assert len(grouped) == 4
    assert {(row["workload_arrival_rate_mean"], row["node_failure_probability"]) for row in grouped} == {
        ("1", "0"),
        ("2", "0.03"),
        ("2", "0.08"),
        ("3", "0"),
    }
    assert {row["contact_profile"] for row in grouped} == {"stress"}


def test_tables_emit_rank_stability_between_regimes(tmp_path: Path) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs_for_rank_stability")
    tables = _load_module("analysis/tables.py", "tables_rank_stability")
    sweep_dir = tmp_path / "runs"
    aggregate_dir = tmp_path / "aggregate_out"
    tables_dir = tmp_path / "tables_out"

    for regime, tier, controller, throughput, latency, slo, scenario in [
        ("F0", "T0", "hpa", 8.0, 10.0, 0.1, "eo_dual_stream"),
        ("F0", "T0", "best_fit", 7.0, 12.0, 0.2, "eo_dual_stream"),
        ("F0_energy", "T1", "hpa", 1.0, 99.0, 0.9, "eo_dual_stream"),
        ("F0_energy", "T1", "best_fit", 1.0, 99.0, 0.9, "eo_dual_stream"),
        ("F1", "T3", "hpa", 6.0, 18.0, 0.3, "eo_dual_stream"),
        ("F1", "T3", "best_fit", 7.5, 13.0, 0.2, "eo_dual_stream"),
        ("F2", "T4", "hpa", 5.0, 20.0, 0.4, "eo_dual_stream"),
        ("F2", "T4", "best_fit", 7.0, 14.0, 0.25, "eo_dual_stream"),
        ("F0", "T0", "hpa", 9.5, 10.0, 0.05, "industrial_iot"),
        ("F0", "T0", "best_fit", 9.0, 11.0, 0.08, "industrial_iot"),
        ("F2", "T4", "hpa", 8.0, 20.0, 0.30, "industrial_iot"),
        ("F2", "T4", "best_fit", 8.5, 15.0, 0.10, "industrial_iot"),
    ]:
        run_dir = sweep_dir / f"{scenario}_{regime}_{controller}"
        _write_summary(
            run_dir,
            scenario=scenario,
            controller=controller,
            seed=0,
            throughput=throughput,
            latency_p95=latency,
            slo_violation_rate=slo,
            aoi_proxy_mean=1.0,
            drop_rate=0.0,
            migrations_total=0.0,
        )
        summary_path = run_dir / "summary.json"
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        payload["metadata"]["realism"] = {
            "declared_tier": tier,
            "detected_tier": tier,
            "label": regime,
        }
        payload["metadata"]["evaluation"] = {
            "declared_regime": "F0" if regime == "F0_energy" else regime,
            "detected_regime": "F0" if regime == "F0_energy" else regime,
            "label": regime,
            "overlays": {
                "energy": regime == "F0_energy",
                "failures": False,
                "contact_mobility": regime in {"F1", "F2"},
                "calibrated_profiles": regime == "F2",
                "calibrated_trace_backed": regime == "F2",
            },
        }
        summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        provenance = {}
        if scenario == "eo_dual_stream" and regime in {"F1", "F2"}:
            provenance = {
                "infrastructure": {
                    "contact_schedule": {
                        "mode": "trace",
                        "path": "src/continuum_bench/calibration/contact_traces/eo_space_ground_small.csv",
                    }
                }
            }
        if regime == "F2":
            provenance["calibration"] = {
                "network_profile": "tracebacked_v1",
                "service_profile_set": f"{scenario}_profile_v1",
            }
        _write_provenance_config(run_dir, provenance)

    aggregate = aggregate_runs.aggregate_sweep(sweep_dir, output_dir=aggregate_dir)
    outputs = tables.build_tables(aggregate["aggregate_csv"], output_dir=tables_dir)

    with Path(outputs["rank_stability_csv"]).open(encoding="utf-8", newline="") as handle:
        stability_rows = list(csv.DictReader(handle))

    hpa_f1_f2 = next(
        row
        for row in stability_rows
        if row["scenario"] == "eo_dual_stream"
        and row["controller"] == "hpa"
        and row["left_regime"] == "F1"
        and row["right_regime"] == "F2"
    )
    assert hpa_f1_f2["left_rank"] == "2"
    assert hpa_f1_f2["right_rank"] == "2"
    assert hpa_f1_f2["rank_delta"] == "0"
    assert hpa_f1_f2["contact_profile"] == "base"
    assert {
        (row["left_regime"], row["right_regime"])
        for row in stability_rows
        if row["scenario"] == "eo_dual_stream" and row["controller"] == "hpa"
    } == {("F1", "F2")}

    industrial_f0_f2 = next(
        row
        for row in stability_rows
        if row["scenario"] == "industrial_iot"
        and row["controller"] == "hpa"
        and row["left_regime"] == "F0"
        and row["right_regime"] == "F2"
    )
    assert industrial_f0_f2["left_rank"] == "1"
    assert industrial_f0_f2["right_rank"] == "2"
    assert industrial_f0_f2["rank_delta"] == "1"
    assert industrial_f0_f2["contact_profile"] == ""


def test_legacy_t4_rows_do_not_imply_contact_mobility_overlay(tmp_path: Path) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs_legacy_t4")
    tables = _load_module("analysis/tables.py", "tables_legacy_t4")
    sweep_dir = tmp_path / "runs"
    aggregate_dir = tmp_path / "aggregate_out"
    tables_dir = tmp_path / "tables_out"

    run_dir = sweep_dir / "split_f2_hpa"
    _write_summary(
        run_dir,
        scenario="split_inference",
        controller="hpa",
        seed=0,
        throughput=10.0,
        latency_p95=20.0,
        slo_violation_rate=0.1,
        aoi_proxy_mean=1.0,
        drop_rate=0.0,
        migrations_total=0.0,
    )
    summary_path = run_dir / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    payload["metadata"]["realism"] = {
        "declared_tier": "T4",
        "detected_tier": "T4",
        "label": "calibrated_trace_backed",
    }
    payload["metadata"].pop("evaluation", None)
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_provenance_config(
        run_dir,
        {
            "calibration": {
                "network_profile": "edge_cloud_tracebacked_v1",
                "service_profile_set": "split_inference_batch_lab_v1",
            }
        },
    )

    aggregate = aggregate_runs.aggregate_sweep(sweep_dir, output_dir=aggregate_dir)
    with Path(aggregate["aggregate_csv"]).open(encoding="utf-8", newline="") as handle:
        aggregate_rows = list(csv.DictReader(handle))

    assert aggregate_rows[0]["evaluation_regime"] == "F2"
    assert aggregate_rows[0]["calibration_overlay"] == "True"
    assert aggregate_rows[0]["contact_mobility_overlay"] == "False"

    outputs = tables.build_tables(aggregate["aggregate_csv"], output_dir=tables_dir)
    with Path(outputs["grouped_csv"]).open(encoding="utf-8", newline="") as handle:
        grouped_rows = list(csv.DictReader(handle))

    assert grouped_rows[0]["evaluation_regime"] == "F2"
    assert grouped_rows[0]["calibration_overlay"] == "True"
    assert grouped_rows[0]["contact_mobility_overlay"] == "False"


def test_aggregate_sweep_raises_on_missing_required_fields(tmp_path: Path) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs_missing_fields")
    sweep_dir = tmp_path / "runs"
    output_dir = tmp_path / "analysis_out"
    run_dir = sweep_dir / "broken_run"

    _write_summary(
        run_dir,
        scenario="eo_dual_stream",
        controller="no_scaling",
        seed=0,
        throughput=10.0,
        latency_p95=2.0,
        slo_violation_rate=0.10,
        aoi_proxy_mean=1.5,
        drop_rate=0.0,
        migrations_total=1.0,
    )

    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    del summary["latency_s"]["p95"]
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match=r"Missing required summary field: latency_s\.p95"):
        aggregate_runs.aggregate_sweep(sweep_dir, output_dir=output_dir)


def test_aggregate_sweep_rejects_old_summaries_without_pending_fields(tmp_path: Path) -> None:
    aggregate_runs = _load_module("analysis/aggregate_runs.py", "aggregate_runs_old_summary")
    sweep_dir = tmp_path / "runs"
    output_dir = tmp_path / "analysis_out"
    run_dir = sweep_dir / "old_protocol_run"

    _write_summary(
        run_dir,
        scenario="eo_dual_stream",
        controller="no_scaling",
        seed=0,
        throughput=10.0,
        latency_p95=2.0,
        slo_violation_rate=0.10,
        aoi_proxy_mean=1.5,
        drop_rate=0.0,
        migrations_total=1.0,
    )

    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for field in ("pending_tasks", "pending_rate", "resolved_rate"):
        del summary["totals"][field]
    for field in ("pending_tasks", "resolved_tasks", "pending_rate", "resolved_rate"):
        del summary["reliability"][field]
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match=r"Missing required summary field: totals\.pending_tasks"):
        aggregate_runs.aggregate_sweep(sweep_dir, output_dir=output_dir)

