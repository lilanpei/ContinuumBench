from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("matplotlib")  # analysis.figures imports matplotlib for rendering

from analysis import figures


def test_eo_trajectory_uses_autoscaling_controllers() -> None:
    rows = [
        {
            "scenario": "eo_dual_stream",
            "evaluation_regime": "F2",
            "controller": controller,
            "seed": "0",
            "run_dir": f"/tmp/{controller}",
        }
        for controller in ["hpa", "keda", "best_fit"]
    ]

    run_dirs = figures._eo_trajectory_run_dirs(rows)

    assert run_dirs == {"HPA": Path("/tmp/hpa"), "KEDA": Path("/tmp/keda")}


def test_stress_rank_summaries_use_contact_and_load_endpoints() -> None:
    contact_rows = _rows_for_rank_change(
        scenario="eo_dual_stream",
        endpoint_key="contact_profile",
        left_value="mild",
        right_value="stress",
        left_order=["hpa", "keda", "learned_stub"],
        right_order=["learned_stub", "keda", "hpa"],
    ) + _rows_for_rank_change(
        scenario="driving_v2x",
        endpoint_key="contact_profile",
        left_value="mild",
        right_value="stress",
        left_order=["hpa", "keda", "learned_stub"],
        right_order=["hpa", "keda", "learned_stub"],
    )
    load_rows = _rows_for_rank_change(
        scenario="split_inference",
        endpoint_key="workload_arrival_rate_mean",
        left_value="3.15",
        right_value="6.3",
        left_order=["hpa", "keda", "learned_stub"],
        right_order=["keda", "learned_stub", "hpa"],
    ) + _rows_for_rank_change(
        scenario="industrial_iot",
        endpoint_key="workload_arrival_rate_mean",
        left_value="1.65",
        right_value="4.95",
        left_order=["hpa", "keda", "learned_stub"],
        right_order=["hpa", "keda", "learned_stub"],
    )

    summaries = figures._stress_rank_summaries(contact_rows, load_rows)

    summary_by_label = {summary["label"]: summary for summary in summaries}
    assert summary_by_label["EO contact"] == {
        "label": "EO contact",
        "stable": 1,
        "changed": 2,
        "total": 3,
    }
    assert summary_by_label["Split load"] == {
        "label": "Split load",
        "stable": 0,
        "changed": 3,
        "total": 3,
    }


def test_control_dimension_summaries_use_matched_placement_reference() -> None:
    rows = [
        _control_dimension_row(
            scenario="eo_dual_stream",
            controller="best_fit",
            valid="False",
            score="10",
            completion="0.0",
            slo="1.0",
        ),
        _control_dimension_row(
            scenario="eo_dual_stream",
            controller="round_robin",
            valid="True",
            score="1",
            completion="0.25",
            slo="0.80",
        ),
        _control_dimension_row(
            scenario="eo_dual_stream",
            controller="hpa",
            valid="True",
            score="2",
            completion="0.55",
            slo="0.30",
        ),
        _control_dimension_row(
            scenario="eo_dual_stream",
            controller="learned_stub",
            valid="True",
            score="3",
            completion="0.58",
            slo="0.25",
        ),
        _control_dimension_row(
            scenario="eo_dual_stream",
            controller="separated_loops",
            valid="True",
            score="4",
            completion="0.70",
            slo="0.20",
        ),
    ]
    for scenario in ["driving_v2x", "split_inference", "industrial_iot"]:
        rows.extend(
            [
                _control_dimension_row(
                    scenario=scenario,
                    controller="best_fit",
                    valid="True",
                    score="1",
                    completion="0.50",
                    slo="0.50",
                ),
                _control_dimension_row(
                    scenario=scenario,
                    controller="hpa",
                    valid="True",
                    score="2",
                    completion="0.60",
                    slo="0.40",
                ),
                _control_dimension_row(
                    scenario=scenario,
                    controller="learned_stub",
                    valid="True",
                    score="3",
                    completion="0.70",
                    slo="0.30",
                ),
                _control_dimension_row(
                    scenario=scenario,
                    controller="separated_loops",
                    valid="True",
                    score="4",
                    completion="0.80",
                    slo="0.20",
                ),
            ]
        )

    summaries = figures._control_dimension_summaries(rows)
    eo = summaries[0]

    assert eo["Placement-only"]["controller"] == "round_robin"
    assert eo["Placement-only"]["completion_ratio"] == pytest.approx(0.25)
    assert eo["Joint heuristic"]["completion_ratio"] == pytest.approx(0.70)
    assert eo["Rule-based controller baselines"]["completion_ratio"] == pytest.approx(0.58)
    assert eo["Rule-based controller baselines"]["slo_reduction"] == pytest.approx(0.55)


def test_controller_overhead_summaries_group_f2_rows_by_family() -> None:
    rows: list[dict[str, str]] = []
    for scenario in figures.SCENARIOS:
        rows.extend(
            [
                _overhead_row(scenario, "best_fit", "F2", "10.0"),
                _overhead_row(scenario, "round_robin", "F2", "14.0"),
                _overhead_row(scenario, "hpa", "F2", "6.0"),
                _overhead_row(scenario, "keda", "F2", "8.0"),
                _overhead_row(scenario, "separated_loops", "F2", "20.0"),
                _overhead_row(scenario, "learned_stub", "F2", "30.0"),
                _overhead_row(scenario, "rl_meta", "F2", "40.0"),
                _overhead_row(scenario, "hpa", "F0", "1000.0"),
            ]
        )

    summaries = figures._controller_overhead_summaries(rows)
    eo = summaries["eo_dual_stream"]

    assert eo["Placement-only"]["mean"] == pytest.approx(12.0)
    assert eo["Placement-only"]["std"] == pytest.approx(2.8284271247461903)
    assert eo["Autoscaling"]["mean"] == pytest.approx(7.0)
    assert eo["Joint heuristic"]["mean"] == pytest.approx(20.0)
    assert eo["Rule-based controller baselines"]["mean"] == pytest.approx(35.0)


def test_delay_breakdown_summaries_select_hpa_components() -> None:
    rows: list[dict[str, str]] = []
    for scenario in figures.SCENARIOS:
        rows.extend(
            [
                _delay_row(
                    scenario=scenario,
                    controller="best_fit",
                    queue="99.0",
                    processing="99.0",
                    transport="99.0",
                    buffer_wait="99.0",
                ),
                _delay_row(
                    scenario=scenario,
                    controller="hpa",
                    queue="3.0",
                    processing="2.0",
                    transport="1.5",
                    buffer_wait="0.5",
                    startup="0.2",
                    migration="0.1",
                ),
            ]
        )

    summaries = figures._delay_breakdown_summaries(rows)

    assert summaries["eo_dual_stream"] == {
        "controller": "hpa",
        "queue_wait": 3.0,
        "processing": 2.0,
        "transport": 1.5,
        "buffer_wait": 0.5,
        "startup": 0.2,
        "migration": 0.1,
    }


def _control_dimension_row(
    *,
    scenario: str,
    controller: str,
    valid: str,
    score: str,
    completion: str,
    slo: str,
) -> dict[str, str]:
    return {
        "scenario": scenario,
        "evaluation_regime": "F2",
        "controller": controller,
        "valid_for_latency_rank": valid,
        "composite_score": score,
        "completion_ratio": completion,
        "slo_violation_rate": slo,
    }


def _overhead_row(
    scenario: str,
    controller: str,
    regime: str,
    overhead: str,
) -> dict[str, str]:
    return {
        "scenario": scenario,
        "evaluation_regime": regime,
        "controller": controller,
        "controller_overhead_p95_ms": overhead,
    }


def _delay_row(
    *,
    scenario: str,
    controller: str,
    queue: str,
    processing: str,
    transport: str,
    buffer_wait: str,
    startup: str = "0.0",
    migration: str = "0.0",
) -> dict[str, str]:
    return {
        "scenario": scenario,
        "evaluation_regime": "F2",
        "controller": controller,
        "composite_score": "1.0",
        "delay_queue_wait_mean_s": queue,
        "delay_processing_mean_s": processing,
        "delay_transport_mean_s": transport,
        "delay_buffer_wait_mean_s": buffer_wait,
        "delay_startup_mean_s": startup,
        "delay_migration_mean_s": migration,
    }


def _rows_for_rank_change(
    *,
    scenario: str,
    endpoint_key: str,
    left_value: str,
    right_value: str,
    left_order: list[str],
    right_order: list[str],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for endpoint_value, order in [(left_value, left_order), (right_value, right_order)]:
        for score, controller in enumerate(reversed(order), start=1):
            row = {
                "scenario": scenario,
                "controller": controller,
                "composite_score": str(score),
                "completion_ratio": "0.5",
                "slo_violation_rate": "0.5",
                "latency_p95_s": "10",
                "buffered_items_peak": "1",
                "valid_for_latency_rank": "True",
                "workload_arrival_rate_mean": "",
                "contact_profile": "",
            }
            row[endpoint_key] = endpoint_value
            rows.append(row)
    return rows
