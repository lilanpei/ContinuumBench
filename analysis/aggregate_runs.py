"""Aggregate offline run summaries into analysis artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Iterable

import yaml


AGGREGATE_FIELDS = [
    "scenario",
    "controller",
    "evaluation_regime",
    "evaluation_label",
    "energy_overlay",
    "failure_overlay",
    "contact_mobility_overlay",
    "calibration_overlay",
    "workload_arrival_rate_mean",
    "failure_mode",
    "node_failure_probability",
    "contact_profile",
    "realism_tier",
    "realism_label",
    "seed",
    "run_id",
    "run_dir",
    "epochs",
    "duration_s",
    "generated_tasks",
    "completed_tasks",
    "completion_ratio",
    "pending_tasks",
    "pending_rate",
    "resolved_rate",
    "throughput_tasks_per_s",
    "latency_p50_s",
    "latency_p95_s",
    "latency_p99_s",
    "latency_mean_s",
    "delay_queue_wait_mean_s",
    "delay_processing_mean_s",
    "delay_transport_mean_s",
    "delay_buffer_wait_mean_s",
    "delay_startup_mean_s",
    "delay_migration_mean_s",
    "deadline_miss_rate",
    "slo_violation_rate",
    "age_at_delivery_p95_s",
    "aoi_proxy_mean_s",
    "drop_rate",
    "expire_rate",
    "buffered_items_peak",
    "active_link_ratio_mean",
    "closed_link_epochs",
    "migrations_total",
    "scaling_delta_abs_total",
    "controller_overhead_p95_ms",
    "cpu_utilization_mean",
    "cpu_utilization_peak",
    "cpu_overcommit_mean",
    "cpu_overcommit_peak",
    "ram_utilization_mean",
    "storage_utilization_mean",
    "energy_total_j",
    "network_energy_total_j",
    "mean_power_w",
    "reliability_success_rate",
    "reliability_failure_rate",
    "reliability_pending_rate",
    "reliability_resolved_rate",
    "failure_event_failed_tasks",
    "controller_reward_mean",
]

GROUPED_METRICS = [
    "throughput_tasks_per_s",
    "completion_ratio",
    "pending_rate",
    "latency_p95_s",
    "slo_violation_rate",
    "aoi_proxy_mean_s",
    "drop_rate",
    "buffered_items_peak",
    "active_link_ratio_mean",
    "closed_link_epochs",
    "cpu_utilization_mean",
    "ram_utilization_mean",
    "energy_total_j",
    "network_energy_total_j",
    "reliability_success_rate",
    "reliability_pending_rate",
    "failure_event_failed_tasks",
    "controller_reward_mean",
    "migrations_total",
    "delay_queue_wait_mean_s",
    "delay_processing_mean_s",
    "delay_transport_mean_s",
    "delay_buffer_wait_mean_s",
    "delay_startup_mean_s",
    "delay_migration_mean_s",
]

REQUIRED_FIELDS = {
    "metadata": ["scenario", "controller", "seed", "run_id", "run_dir"],
    "totals": [
        "epochs",
        "duration_s",
        "generated_tasks",
        "completed_tasks",
        "completion_ratio",
        "pending_tasks",
        "pending_rate",
        "resolved_rate",
        "throughput_tasks_per_s",
    ],
    "latency_s": ["p50", "p95", "p99", "mean"],
    "qos": ["deadline_miss_rate", "slo_violation_rate"],
    "freshness": ["age_at_delivery_p95", "aoi_proxy_mean"],
    "resource_utilization": [
        "cpu_mean",
        "cpu_peak",
        "cpu_overcommit_mean",
        "cpu_overcommit_peak",
        "ram_mean",
        "storage_mean",
    ],
    "energy": ["total_j", "network_total_j", "mean_power_w"],
    "reliability": [
        "success_rate",
        "failure_rate",
        "pending_rate",
        "resolved_rate",
        "failure_event_failed_tasks",
    ],
    "controller_reward": ["mean"],
    "store_forward": ["drop_rate", "expire_rate"],
    "contact": ["active_link_ratio_mean", "closed_link_epochs"],
    "stability": ["migrations_total", "scaling_delta_abs_total"],
    "controller_overhead_ms": ["p95"],
}

DELAY_COMPONENT_FIELDS = {
    "queue_wait_s": "delay_queue_wait_mean_s",
    "processing_s": "delay_processing_mean_s",
    "transport_s": "delay_transport_mean_s",
    "buffer_wait_s": "delay_buffer_wait_mean_s",
    "startup_delay_s": "delay_startup_mean_s",
    "migration_delay_s": "delay_migration_mean_s",
}


def aggregate_sweep(
    sweep_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    group_by: Iterable[str] = ("scenario", "controller"),
) -> dict[str, str]:
    """Aggregate one sweep directory into CSV and grouped JSON outputs."""

    sweep_path = Path(sweep_dir)
    out_path = Path(output_dir) if output_dir is not None else sweep_path / "analysis"
    out_path.mkdir(parents=True, exist_ok=True)

    rows = _load_rows(sweep_path)
    if not rows:
        raise ValueError(f"No summary.json files found under {sweep_path}")

    aggregate_csv = out_path / "aggregate.csv"
    grouped_json = out_path / "grouped_summary.json"

    _write_aggregate_csv(aggregate_csv, rows)
    _write_grouped_json(grouped_json, rows, list(group_by))

    return {
        "aggregate_csv": str(aggregate_csv),
        "grouped_json": str(grouped_json),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the aggregate-runs command-line entry point."""

    parser = argparse.ArgumentParser(description="Aggregate ContinuumBench run summaries")
    parser.add_argument("--sweep-dir", required=True, help="Directory containing run subdirectories")
    parser.add_argument("--out", required=True, help="Directory for aggregate analysis outputs")
    args = parser.parse_args(argv)

    outputs = aggregate_sweep(args.sweep_dir, output_dir=args.out)
    print(f"Aggregate CSV: {outputs['aggregate_csv']}")
    print(f"Grouped JSON: {outputs['grouped_json']}")
    return 0


def _load_rows(sweep_dir: Path) -> list[dict[str, Any]]:
    """Load and sort summary rows from a sweep directory."""

    rows: list[dict[str, Any]] = []
    for summary_path in sorted(sweep_dir.rglob("summary.json")):
        with summary_path.open(encoding="utf-8") as handle:
            summary = json.load(handle)
        rows.append(_summary_to_row(summary, summary_path))

    rows.sort(
        key=lambda row: (
            str(row["scenario"]),
            str(row["controller"]),
            int(row["seed"]),
            str(row["run_id"]),
        )
    )
    return rows


def _summary_to_row(summary: dict[str, Any], summary_path: Path) -> dict[str, Any]:
    """Flatten one summary payload into an aggregate CSV row."""

    metadata = _require_section(summary, "metadata")
    totals = _require_section(summary, "totals")
    latency = _require_section(summary, "latency_s")
    qos = _require_section(summary, "qos")
    freshness = _require_section(summary, "freshness")
    resource_utilization = _require_section(summary, "resource_utilization")
    energy = _require_section(summary, "energy")
    reliability = _require_section(summary, "reliability")
    controller_reward = _require_section(summary, "controller_reward")
    store_forward = _require_section(summary, "store_forward")
    contact = _optional_section(summary, "contact")
    stability = _require_section(summary, "stability")
    controller_overhead = _require_section(summary, "controller_overhead_ms")
    delay_breakdown = _optional_section(summary, "delay_breakdown_s")

    run_dir = _require_value(metadata, "run_dir", "metadata")
    run_id = _require_value(metadata, "run_id", "metadata")
    realism = metadata.get("realism", {})
    if not isinstance(realism, dict):
        realism = {}
    evaluation = metadata.get("evaluation", {})
    if not isinstance(evaluation, dict):
        evaluation = {}
    evaluation_fields = _evaluation_fields(evaluation, realism)
    experiment_fields = _experiment_setting_fields(_load_provenance_config(summary_path))

    return {
        "scenario": str(_require_value(metadata, "scenario", "metadata")),
        "controller": str(_require_value(metadata, "controller", "metadata")),
        **evaluation_fields,
        **experiment_fields,
        "realism_tier": str(realism.get("detected_tier", "")),
        "realism_label": str(realism.get("label", "")),
        "seed": int(_require_value(metadata, "seed", "metadata")),
        "run_id": str(run_id),
        "run_dir": str(run_dir),
        "epochs": int(_require_value(totals, "epochs", "totals")),
        "duration_s": float(_require_value(totals, "duration_s", "totals")),
        "generated_tasks": int(_require_value(totals, "generated_tasks", "totals")),
        "completed_tasks": int(_require_value(totals, "completed_tasks", "totals")),
        "completion_ratio": float(_require_value(totals, "completion_ratio", "totals")),
        "pending_tasks": int(_require_value(totals, "pending_tasks", "totals")),
        "pending_rate": float(_require_value(totals, "pending_rate", "totals")),
        "resolved_rate": float(_require_value(totals, "resolved_rate", "totals")),
        "throughput_tasks_per_s": float(_require_value(totals, "throughput_tasks_per_s", "totals")),
        "latency_p50_s": float(_require_value(latency, "p50", "latency_s")),
        "latency_p95_s": float(_require_value(latency, "p95", "latency_s")),
        "latency_p99_s": float(_require_value(latency, "p99", "latency_s")),
        "latency_mean_s": float(_require_value(latency, "mean", "latency_s")),
        **_delay_breakdown_fields(delay_breakdown),
        "deadline_miss_rate": float(_require_value(qos, "deadline_miss_rate", "qos")),
        "slo_violation_rate": float(_require_value(qos, "slo_violation_rate", "qos")),
        "age_at_delivery_p95_s": float(_require_value(freshness, "age_at_delivery_p95", "freshness")),
        "aoi_proxy_mean_s": float(_require_value(freshness, "aoi_proxy_mean", "freshness")),
        "drop_rate": float(_require_value(store_forward, "drop_rate", "store_forward")),
        "expire_rate": float(_require_value(store_forward, "expire_rate", "store_forward")),
        "buffered_items_peak": float(store_forward.get("buffered_items_peak", 0.0)),
        "active_link_ratio_mean": float(contact.get("active_link_ratio_mean", 0.0)),
        "closed_link_epochs": int(contact.get("closed_link_epochs", 0)),
        "migrations_total": float(_require_value(stability, "migrations_total", "stability")),
        "scaling_delta_abs_total": float(_require_value(stability, "scaling_delta_abs_total", "stability")),
        "controller_overhead_p95_ms": float(_require_value(controller_overhead, "p95", "controller_overhead_ms")),
        "cpu_utilization_mean": float(_require_value(resource_utilization, "cpu_mean", "resource_utilization")),
        "cpu_utilization_peak": float(_require_value(resource_utilization, "cpu_peak", "resource_utilization")),
        "cpu_overcommit_mean": float(
            _require_value(resource_utilization, "cpu_overcommit_mean", "resource_utilization")
        ),
        "cpu_overcommit_peak": float(
            _require_value(resource_utilization, "cpu_overcommit_peak", "resource_utilization")
        ),
        "ram_utilization_mean": float(_require_value(resource_utilization, "ram_mean", "resource_utilization")),
        "storage_utilization_mean": float(
            _require_value(resource_utilization, "storage_mean", "resource_utilization")
        ),
        "energy_total_j": float(_require_value(energy, "total_j", "energy")),
        "network_energy_total_j": float(_require_value(energy, "network_total_j", "energy")),
        "mean_power_w": float(_require_value(energy, "mean_power_w", "energy")),
        "reliability_success_rate": float(_require_value(reliability, "success_rate", "reliability")),
        "reliability_failure_rate": float(_require_value(reliability, "failure_rate", "reliability")),
        "reliability_pending_rate": float(_require_value(reliability, "pending_rate", "reliability")),
        "reliability_resolved_rate": float(_require_value(reliability, "resolved_rate", "reliability")),
        "failure_event_failed_tasks": int(
            _require_value(reliability, "failure_event_failed_tasks", "reliability")
        ),
        "controller_reward_mean": float(_require_value(controller_reward, "mean", "controller_reward")),
}


def _delay_breakdown_fields(delay_breakdown: dict[str, Any]) -> dict[str, float]:
    """Flatten mean delay components for aggregate CSV output."""

    fields: dict[str, float] = {}
    for component, field in DELAY_COMPONENT_FIELDS.items():
        component_stats = delay_breakdown.get(component, {})
        if not isinstance(component_stats, dict):
            component_stats = {}
        fields[field] = float(component_stats.get("mean", 0.0))
    return fields


def _load_provenance_config(summary_path: Path) -> dict[str, Any]:
    """Load the rendered run config copied into the run provenance directory."""

    config_path = summary_path.parent / "provenance" / "config.yaml"
    if not config_path.exists():
        return {}
    with config_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return payload if isinstance(payload, dict) else {}


def _experiment_setting_fields(config: dict[str, Any]) -> dict[str, str]:
    """Return configured workload and failure settings used for sensitivity grouping."""

    reliability = _as_dict(config.get("reliability"))
    failures = _as_dict(reliability.get("failures"))
    infrastructure = _as_dict(config.get("infrastructure"))
    contact_schedule = _as_dict(infrastructure.get("contact_schedule"))
    return {
        "workload_arrival_rate_mean": _format_optional_float(
            _workload_arrival_rate_mean(_as_dict(config.get("workload")))
        ),
        "failure_mode": str(failures.get("mode", "")),
        "node_failure_probability": _format_optional_float(
            _optional_float(failures.get("node_failure_probability"))
        ),
        "contact_profile": _contact_profile_label(contact_schedule),
    }


def _contact_profile_label(contact_schedule: dict[str, Any]) -> str:
    """Return a compact label for the configured contact schedule."""

    path = str(contact_schedule.get("path", ""))
    if not path:
        return ""
    stem = Path(path).stem
    if stem.endswith("_mild"):
        return "mild"
    if stem.endswith("_stress"):
        return "stress"
    return "base"


def _workload_arrival_rate_mean(workload: dict[str, Any]) -> float | None:
    """Return the configured mean source arrival rate per epoch across streams."""

    streams = _as_dict(workload.get("streams"))
    rates: list[float] = []
    for stream_config in streams.values():
        rate = _stream_arrival_rate_mean(_as_dict(stream_config))
        if rate is not None:
            rates.append(rate)
    if not rates:
        return None
    return sum(rates)


def _stream_arrival_rate_mean(stream_config: dict[str, Any]) -> float | None:
    """Return the configured mean arrival rate for one workload stream."""

    if not stream_config:
        return None
    if "rate" in stream_config:
        return max(0.0, float(stream_config["rate"]))

    generator = str(stream_config.get("generator", "piecewise")).lower()
    if generator in {"piecewise", "regime", "regimes"}:
        total_epochs = 0
        weighted_rate = 0.0
        for segment in stream_config.get("segments", []):
            if not isinstance(segment, dict):
                continue
            epochs = max(1, int(segment.get("epochs", 1)))
            rate = max(0.0, float(segment.get("rate", 0.0)))
            total_epochs += epochs
            weighted_rate += epochs * rate
        if total_epochs <= 0:
            return None
        return weighted_rate / total_epochs

    if generator == "mmpp":
        low_rate = max(0.0, float(stream_config.get("low_rate", 0.5)))
        high_rate = max(0.0, float(stream_config.get("high_rate", 5.0)))
        p_low_to_high = max(0.0, float(stream_config.get("p_low_to_high", 0.1)))
        p_high_to_low = max(0.0, float(stream_config.get("p_high_to_low", 0.3)))
        transition_sum = p_low_to_high + p_high_to_low
        if transition_sum <= 0.0:
            return high_rate if str(stream_config.get("initial_state", "low")) == "high" else low_rate
        high_probability = p_low_to_high / transition_sum
        return (1.0 - high_probability) * low_rate + high_probability * high_rate

    return None


def _optional_float(value: Any) -> float | None:
    """Return a float when the value is present and parseable."""

    if value in (None, ""):
        return None
    return float(value)


def _format_optional_float(value: float | None) -> str:
    """Format optional numeric settings compactly for CSV grouping fields."""

    if value is None:
        return ""
    return f"{float(value):.12g}"


def _evaluation_fields(evaluation: dict[str, Any], realism: dict[str, Any]) -> dict[str, Any]:
    """Return evaluation-regime fields, deriving them from legacy metadata if needed."""

    overlays = evaluation.get("overlays", {})
    if not isinstance(overlays, dict) or not overlays:
        overlays = _overlays_from_legacy_features(realism)

    detected_regime = str(evaluation.get("detected_regime") or "")
    if not detected_regime:
        detected_regime = _legacy_tier_to_regime(str(realism.get("detected_tier", "")))

    return {
        "evaluation_regime": detected_regime,
        "evaluation_label": str(evaluation.get("label") or _regime_label(detected_regime)),
        "energy_overlay": bool(overlays.get("energy")),
        "failure_overlay": bool(overlays.get("failures")),
        "contact_mobility_overlay": bool(overlays.get("contact_mobility")),
        "calibration_overlay": bool(overlays.get("calibrated_profiles")),
    }


def _overlays_from_legacy_features(realism: dict[str, Any]) -> dict[str, bool]:
    """Map legacy realism feature names to evaluation overlays."""

    features = realism.get("features", {})
    if not isinstance(features, dict):
        features = {}
    legacy_tier = str(realism.get("detected_tier", ""))
    return {
        "energy": bool(features.get("energy_aware")) or legacy_tier == "T1",
        "failures": bool(features.get("failure_aware")) or legacy_tier == "T2",
        "contact_mobility": bool(features.get("mobility_contact_trace")) or legacy_tier == "T3",
        "calibrated_profiles": bool(features.get("calibrated_trace_backed")) or legacy_tier == "T4",
    }


def _legacy_tier_to_regime(tier: str) -> str:
    """Map a legacy T0-T4 label to the evaluation-regime axis."""

    if tier == "T4":
        return "F2"
    if tier == "T3":
        return "F1"
    return "F0"


def _regime_label(regime: str) -> str:
    """Return a stable label for an F0-F2 evaluation regime."""

    return {
        "F0": "deterministic_synthetic",
        "F1": "contact_mobility_aware",
        "F2": "calibrated_trace_backed",
    }.get(regime, "")


def _write_aggregate_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write flattened run rows to the aggregate CSV file."""

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=AGGREGATE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in AGGREGATE_FIELDS})


def _write_grouped_json(path: Path, rows: list[dict[str, Any]], group_by: list[str]) -> None:
    """Write grouped metric summaries as JSON."""

    grouped_rows: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(row[field] for field in group_by)
        grouped_rows.setdefault(key, []).append(row)

    payload = {
        "group_by": group_by,
        "groups": [],
    }

    for key in sorted(grouped_rows):
        group_rows = grouped_rows[key]
        group_entry = {field: value for field, value in zip(group_by, key)}
        group_entry["runs"] = len(group_rows)
        group_entry["metrics"] = {
            metric: _metric_stats(group_rows, metric) for metric in GROUPED_METRICS
        }
        payload["groups"].append(group_entry)

    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _metric_stats(rows: list[dict[str, Any]], metric: str) -> dict[str, float]:
    """Compute rounded min, max, and mean values for one metric."""

    values = [float(row[metric]) for row in rows]
    return {
        "mean": _round_value(sum(values) / len(values)),
        "min": _round_value(min(values)),
        "max": _round_value(max(values)),
    }


def _round_value(value: float) -> float:
    """Round a numeric value to a stable JSON precision."""

    return round(float(value), 10)


def _as_dict(value: Any) -> dict[str, Any]:
    """Return the value as a dict when possible."""

    if isinstance(value, dict):
        return value
    return {}


def _require_section(summary: dict[str, Any], section: str) -> dict[str, Any]:
    """Return a required summary section after validating its fields."""

    mapping = _as_dict(summary.get(section))
    if not mapping:
        raise ValueError(f"Missing required summary field: {section}")
    for field in REQUIRED_FIELDS.get(section, []):
        _require_value(mapping, field, section)
    return mapping


def _optional_section(summary: dict[str, Any], section: str) -> dict[str, Any]:
    """Return an optional summary section when present."""

    return _as_dict(summary.get(section))


def _require_value(mapping: dict[str, Any], field: str, section: str) -> Any:
    """Return a required field from a summary section."""

    if field not in mapping:
        raise ValueError(f"Missing required summary field: {section}.{field}")
    return mapping[field]


if __name__ == "__main__":
    raise SystemExit(main())
