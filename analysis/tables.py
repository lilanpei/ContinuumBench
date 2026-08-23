"""Build publication tables from aggregate ContinuumBench sweep outputs."""

from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


NUMERIC_FIELDS = [
    "completion_ratio",
    "pending_rate",
    "resolved_rate",
    "throughput_tasks_per_s",
    "latency_p95_s",
    "slo_violation_rate",
    "drop_rate",
    "expire_rate",
    "buffered_items_peak",
    "active_link_ratio_mean",
    "closed_link_epochs",
    "energy_total_j",
    "network_energy_total_j",
    "reliability_success_rate",
    "reliability_failure_rate",
    "reliability_pending_rate",
    "reliability_resolved_rate",
    "failure_event_failed_tasks",
    "migrations_total",
    "scaling_delta_abs_total",
    "controller_overhead_p95_ms",
    "delay_queue_wait_mean_s",
    "delay_processing_mean_s",
    "delay_transport_mean_s",
    "delay_buffer_wait_mean_s",
    "delay_startup_mean_s",
    "delay_migration_mean_s",
    "controller_reward_mean",
]

GROUP_FIELDS = [
    "scenario",
    "evaluation_regime",
    "energy_overlay",
    "failure_overlay",
    "contact_mobility_overlay",
    "calibration_overlay",
    "workload_arrival_rate_mean",
    "failure_mode",
    "node_failure_probability",
    "contact_profile",
    "controller",
]
RANK_BUCKET_FIELDS = GROUP_FIELDS[:-1]


def build_tables(
    aggregate_csv: str | Path,
    *,
    output_dir: str | Path,
    min_completion_for_latency_rank: float = 0.1,
) -> dict[str, str]:
    """Create grouped and ranking CSVs from one aggregate output."""

    aggregate_path = Path(aggregate_csv)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    rows = _read_rows(aggregate_path)
    grouped = _group_rows(
        rows,
        min_completion_for_latency_rank=float(min_completion_for_latency_rank),
    )
    grouped_path = out_path / "grouped_strict.csv"
    ranking_path = out_path / "rankings.csv"
    rank_stability_path = out_path / "rank_stability.csv"

    _write_csv(grouped_path, grouped, _grouped_fields())
    rankings = _rank_rows(grouped)
    _write_csv(ranking_path, rankings, _ranking_fields())
    _write_csv(rank_stability_path, _rank_stability_rows(rankings), _rank_stability_fields())

    return {
        "grouped_csv": str(grouped_path),
        "ranking_csv": str(ranking_path),
        "rank_stability_csv": str(rank_stability_path),
    }


def main(argv: list[str] | None = None) -> int:
    """Run the publication-table command-line entry point."""

    parser = argparse.ArgumentParser(description="Build publication ContinuumBench tables")
    parser.add_argument("--aggregate-csv", required=True, help="aggregate.csv from analysis/aggregate_runs.py")
    parser.add_argument("--out", required=True, help="Directory for publication table outputs")
    parser.add_argument(
        "--min-completion-for-latency-rank",
        type=float,
        default=0.1,
        help="Minimum completion ratio required before latency-only rank is considered valid",
    )
    args = parser.parse_args(argv)

    outputs = build_tables(
        args.aggregate_csv,
        output_dir=args.out,
        min_completion_for_latency_rank=args.min_completion_for_latency_rank,
    )
    print(f"Grouped strict CSV: {outputs['grouped_csv']}")
    print(f"Ranking CSV: {outputs['ranking_csv']}")
    print(f"Rank stability CSV: {outputs['rank_stability_csv']}")
    return 0


def _read_rows(path: Path) -> list[dict[str, Any]]:
    """Read aggregate rows and convert publication metric fields to floats."""

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        _ensure_evaluation_fields(row)
        for field in NUMERIC_FIELDS:
            row[field] = float(row[field])
        row["seed"] = int(row["seed"])
    return rows


def _group_rows(
    rows: list[dict[str, Any]],
    *,
    min_completion_for_latency_rank: float,
) -> list[dict[str, Any]]:
    """Group aggregate rows by scenario, evaluation regime, overlays, and controller."""

    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[tuple(str(row[field]) for field in GROUP_FIELDS)].append(row)

    grouped: list[dict[str, Any]] = []
    for key in sorted(buckets):
        group_rows = buckets[key]
        record: dict[str, Any] = {
            field: key[idx] for idx, field in enumerate(GROUP_FIELDS)
        }
        record.update(
            {
                "realism_tier": _legacy_tier_from_row(group_rows[0]),
                "n": len(group_rows),
            }
        )
        for field in NUMERIC_FIELDS:
            values = [float(row[field]) for row in group_rows]
            record[field] = statistics.mean(values)
            record[f"{field}_std"] = statistics.stdev(values) if len(values) > 1 else 0.0

        record["valid_for_latency_rank"] = (
            float(record["completion_ratio"]) >= min_completion_for_latency_rank
        )
        record["composite_score"] = _composite_score(record)
        grouped.append(record)

    return grouped


def _composite_score(record: dict[str, Any]) -> float:
    """Return the completion-aware score used for ranking table consistency."""

    return (
        float(record["completion_ratio"])
        - 0.5 * float(record["pending_rate"])
        - float(record["slo_violation_rate"])
        - 0.01 * float(record["latency_p95_s"])
        - 0.001 * float(record["migrations_total"])
    )


def _rank_rows(grouped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank controllers within each scenario, evaluation regime, and overlay setting."""

    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in grouped:
        buckets[tuple(str(row[field]) for field in RANK_BUCKET_FIELDS)].append(row)

    rankings: list[dict[str, Any]] = []
    for key in sorted(buckets):
        bucket_context = {field: key[idx] for idx, field in enumerate(RANK_BUCKET_FIELDS)}
        score_sorted = sorted(
            buckets[key],
            key=lambda row: (
                bool(row.get("valid_for_latency_rank", False)),
                float(row["composite_score"]),
            ),
            reverse=True,
        )
        latency_candidates = [
            row for row in buckets[key] if bool(row.get("valid_for_latency_rank", False))
        ]
        latency_sorted = sorted(
            latency_candidates,
            key=lambda row: (float(row["latency_p95_s"]), float(row["slo_violation_rate"])),
        )
        latency_rank_by_controller = {
            str(row["controller"]): rank for rank, row in enumerate(latency_sorted, start=1)
        }

        for score_rank, row in enumerate(score_sorted, start=1):
            controller = str(row["controller"])
            rankings.append(
                {
                    **bucket_context,
                    "realism_tier": row["realism_tier"],
                    "controller": controller,
                    "score_rank": score_rank,
                    "latency_rank_if_valid": latency_rank_by_controller.get(controller, ""),
                    "valid_for_latency_rank": row["valid_for_latency_rank"],
                    "composite_score": row["composite_score"],
                    "completion_ratio": row["completion_ratio"],
                    "pending_rate": row["pending_rate"],
                    "latency_p95_s": row["latency_p95_s"],
                    "slo_violation_rate": row["slo_violation_rate"],
                    "buffered_items_peak": row["buffered_items_peak"],
                    "active_link_ratio_mean": row["active_link_ratio_mean"],
                    "closed_link_epochs": row["closed_link_epochs"],
                    "energy_total_j": row["energy_total_j"],
                    "reliability_success_rate": row["reliability_success_rate"],
                    "migrations_total": row["migrations_total"],
                }
            )

    return rankings


def _rank_stability_rows(rankings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare score-rank stability across comparable controller buckets."""

    by_context: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rankings:
        if row.get("energy_overlay") == "True" or row.get("failure_overlay") == "True":
            continue
        context = (
            str(row["scenario"]),
            str(row["workload_arrival_rate_mean"]),
            str(row["failure_mode"]),
            str(row["node_failure_probability"]),
            _normalized_contact_profile(row),
            str(row["controller"]),
        )
        by_context[context].append(row)

    rows: list[dict[str, Any]] = []
    for context, context_rows in sorted(by_context.items()):
        by_regime = {str(row["evaluation_regime"]): row for row in context_rows}
        for left, right in (("F0", "F1"), ("F1", "F2"), ("F0", "F2")):
            if left not in by_regime or right not in by_regime:
                continue
            left_row = by_regime[left]
            right_row = by_regime[right]
            rows.append(
                {
                    "scenario": context[0],
                    "workload_arrival_rate_mean": context[1],
                    "failure_mode": context[2],
                    "node_failure_probability": context[3],
                    "contact_profile": context[4],
                    "controller": context[5],
                    "left_regime": left,
                    "right_regime": right,
                    "left_rank": left_row["score_rank"],
                    "right_rank": right_row["score_rank"],
                    "rank_delta": int(right_row["score_rank"]) - int(left_row["score_rank"]),
                    "completion_delta": float(right_row["completion_ratio"])
                    - float(left_row["completion_ratio"]),
                    "slo_delta": float(right_row["slo_violation_rate"])
                    - float(left_row["slo_violation_rate"]),
                    "latency_p95_delta_s": float(right_row["latency_p95_s"])
                    - float(left_row["latency_p95_s"]),
                    "buffered_items_delta": float(right_row["buffered_items_peak"])
                    - float(left_row["buffered_items_peak"]),
                }
            )
    return rows


def _normalized_contact_profile(row: dict[str, Any]) -> str:
    """Return the comparable contact-profile label for regime-rank analysis."""

    return str(row.get("contact_profile", ""))


def _grouped_fields() -> list[str]:
    """Return CSV fields for grouped publication metrics."""

    fields = [
        "scenario",
        "evaluation_regime",
        "energy_overlay",
        "failure_overlay",
        "contact_mobility_overlay",
        "calibration_overlay",
        "workload_arrival_rate_mean",
        "failure_mode",
        "node_failure_probability",
        "contact_profile",
        "realism_tier",
        "controller",
        "n",
        "valid_for_latency_rank",
        "composite_score",
    ]
    for field in NUMERIC_FIELDS:
        fields.extend([field, f"{field}_std"])
    return fields


def _ranking_fields() -> list[str]:
    """Return CSV fields for publication rankings."""

    return [
        "scenario",
        "evaluation_regime",
        "energy_overlay",
        "failure_overlay",
        "contact_mobility_overlay",
        "calibration_overlay",
        "workload_arrival_rate_mean",
        "failure_mode",
        "node_failure_probability",
        "contact_profile",
        "realism_tier",
        "controller",
        "score_rank",
        "latency_rank_if_valid",
        "valid_for_latency_rank",
        "composite_score",
        "completion_ratio",
        "pending_rate",
        "latency_p95_s",
        "slo_violation_rate",
        "buffered_items_peak",
        "active_link_ratio_mean",
        "closed_link_epochs",
        "energy_total_j",
        "reliability_success_rate",
        "migrations_total",
    ]


def _rank_stability_fields() -> list[str]:
    """Return CSV fields for rank-stability comparisons."""

    return [
        "scenario",
        "workload_arrival_rate_mean",
        "failure_mode",
        "node_failure_probability",
        "contact_profile",
        "controller",
        "left_regime",
        "right_regime",
        "left_rank",
        "right_rank",
        "rank_delta",
        "completion_delta",
        "slo_delta",
        "latency_p95_delta_s",
        "buffered_items_delta",
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    """Write rows to CSV with a stable field order."""

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fieldnames})


def _ensure_evaluation_fields(row: dict[str, Any]) -> None:
    """Populate F0-F2 fields when reading aggregate CSVs from legacy runs."""

    legacy_tier = str(row.get("realism_tier", ""))
    if not row.get("evaluation_regime"):
        row["evaluation_regime"] = _legacy_tier_to_regime(legacy_tier)
    if not row.get("energy_overlay"):
        row["energy_overlay"] = "True" if legacy_tier == "T1" else "False"
    if not row.get("failure_overlay"):
        row["failure_overlay"] = "True" if legacy_tier == "T2" else "False"
    if not row.get("contact_mobility_overlay"):
        row["contact_mobility_overlay"] = "True" if legacy_tier == "T3" else "False"
    if not row.get("calibration_overlay"):
        row["calibration_overlay"] = (
            "True" if row.get("evaluation_regime") == "F2" else "False"
        )
    row.setdefault("workload_arrival_rate_mean", "")
    row.setdefault("failure_mode", "")
    row.setdefault("node_failure_probability", "")
    row.setdefault("contact_profile", "")
    row.setdefault("buffered_items_peak", 0.0)
    row.setdefault("active_link_ratio_mean", 0.0)
    row.setdefault("closed_link_epochs", 0.0)


def _legacy_tier_to_regime(tier: str) -> str:
    """Map a legacy T0-T4 label to the evaluation-regime axis."""

    if tier == "T4":
        return "F2"
    if tier == "T3":
        return "F1"
    return "F0"


def _legacy_tier_from_row(row: dict[str, Any]) -> str:
    """Return a fallback legacy tier for backward-compatible outputs."""

    return str(row.get("realism_tier", ""))


if __name__ == "__main__":
    raise SystemExit(main())
