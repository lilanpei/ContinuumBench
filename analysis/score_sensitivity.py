"""Check completion-aware score sensitivity on grouped paper results.

This is the score-robustness check of the ContinuumBench paper (Section 5). It perturbs the four
weights of the completion-aware score over an 81-point grid and reports, for each scenario-regime
context of the main comparison, whether the selected representative of a family changes. The
scale-capable family here is HPA, KEDA and the joint heuristic (``separated_loops``); the
placement-only family is round-robin, best-fit, cost-greedy and the MILP.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


SCENARIOS = ["eo_dual_stream", "driving_v2x", "split_inference", "industrial_iot"]
REGIMES = ["F0", "F2"]
REGIME_NAMES = {"F0": "R1", "F2": "R2"}
SCENARIO_NAMES = {
    "eo_dual_stream": "EO dual stream",
    "driving_v2x": "Driving V2X",
    "split_inference": "Split inference",
    "industrial_iot": "Industrial IoT",
}
SCALE_CAPABLE = {"hpa", "keda", "separated_loops"}
PLACEMENT_ONLY = {
    "best_fit",
    "round_robin",
    "placement_only:cost_greedy",
    "placement_only:milp",
}
DEFAULT_WEIGHTS = {
    "pending": 0.5,
    "slo": 1.0,
    "latency_p95_s": 0.01,
    "migrations": 0.001,
}
WEIGHT_MULTIPLIERS = {
    "pending": [0.5, 1.0, 1.5],
    "slo": [0.75, 1.0, 1.25],
    "latency_p95_s": [0.5, 1.0, 1.5],
    "migrations": [0.5, 1.0, 1.5],
}


def main(argv: list[str] | None = None) -> int:
    """Run the score-sensitivity command-line entry point."""

    parser = argparse.ArgumentParser(
        description=(
            "Perturb the completion-aware score weights and report whether the "
            "family-level controller comparison changes."
        )
    )
    parser.add_argument(
        "--grouped-csv",
        default="runs/main/analysis/grouped_strict.csv",
        help="Grouped strict CSV from analysis/tables.py.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path; defaults to score_sensitivity.json beside --grouped-csv.",
    )
    args = parser.parse_args(argv)

    grouped_csv = Path(args.grouped_csv)
    summary = score_sensitivity(grouped_csv)
    out_path = Path(args.out) if args.out else grouped_csv.parent / "score_sensitivity.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    changed = [
        f"{SCENARIO_NAMES.get(context['scenario'], context['scenario'])} "
        f"{REGIME_NAMES.get(context['evaluation_regime'], context['evaluation_regime'])}"
        for context in summary["contexts"]
        if context["controller_choice_changed"]
    ]
    reduction = summary["min_slo_reduction_placement_minus_scale"]
    print(
        f"Over the {summary['weight_grid_size']}-point weight grid, the selected representatives "
        f"change in {len(changed)} of {summary['total_contexts']} scenario-regime contexts"
        + (f" ({', '.join(changed)})" if changed else "")
        + "; the scale-capable representative "
        + ("has" if reduction > 0 else "does not always have")
        + f" the lower SLO-violation rate (smallest reduction {reduction:.2f})."
    )
    print(f"Score-sensitivity summary: {out_path}")
    return 0


def score_sensitivity(grouped_csv: Path) -> dict[str, Any]:
    """Return score-weight sensitivity over the main F0/F2 comparison."""

    rows = _read_csv(grouped_csv)
    weight_grid = list(_weight_grid())
    contexts: list[dict[str, Any]] = []
    min_completion_delta = float("inf")
    min_slo_reduction = float("inf")

    for scenario in SCENARIOS:
        for regime in REGIMES:
            subset = [
                row
                for row in rows
                if row["scenario"] == scenario and row["evaluation_regime"] == regime
            ]
            scale_rows = [row for row in subset if row["controller"] in SCALE_CAPABLE]
            placement_rows = [
                row
                for row in subset
                if row["controller"] in PLACEMENT_ONLY
                and row["valid_for_latency_rank"] == "True"
            ]
            selected_pairs: set[tuple[str, str]] = set()
            for weights in weight_grid:
                scale_row = max(scale_rows, key=lambda row: _score(row, weights))
                placement_row = max(placement_rows, key=lambda row: _score(row, weights))
                selected_pairs.add((scale_row["controller"], placement_row["controller"]))
                completion_delta = _as_float(scale_row, "completion_ratio") - _as_float(
                    placement_row, "completion_ratio"
                )
                slo_reduction = _as_float(placement_row, "slo_violation_rate") - _as_float(
                    scale_row, "slo_violation_rate"
                )
                min_completion_delta = min(min_completion_delta, completion_delta)
                min_slo_reduction = min(min_slo_reduction, slo_reduction)

            contexts.append(
                {
                    "scenario": scenario,
                    "evaluation_regime": regime,
                    "selected_pairs": [
                        {"scale_capable": scale, "placement_only": placement}
                        for scale, placement in sorted(selected_pairs)
                    ],
                    "controller_choice_changed": len(selected_pairs) > 1,
                }
            )

    changed_contexts = [
        context for context in contexts if bool(context["controller_choice_changed"])
    ]
    return {
        "default_weights": DEFAULT_WEIGHTS,
        "weight_multipliers": WEIGHT_MULTIPLIERS,
        "weight_grid_size": len(weight_grid),
        "contexts": contexts,
        "contexts_with_controller_choice_changes": len(changed_contexts),
        "total_contexts": len(contexts),
        "min_completion_delta_scale_minus_placement": min_completion_delta,
        "min_slo_reduction_placement_minus_scale": min_slo_reduction,
    }


def _weight_grid() -> list[dict[str, float]]:
    """Return the default perturbation grid used for the sensitivity check."""

    weights: list[dict[str, float]] = []
    for pending in WEIGHT_MULTIPLIERS["pending"]:
        for slo in WEIGHT_MULTIPLIERS["slo"]:
            for latency in WEIGHT_MULTIPLIERS["latency_p95_s"]:
                for migrations in WEIGHT_MULTIPLIERS["migrations"]:
                    weights.append(
                        {
                            "pending": DEFAULT_WEIGHTS["pending"] * pending,
                            "slo": DEFAULT_WEIGHTS["slo"] * slo,
                            "latency_p95_s": DEFAULT_WEIGHTS["latency_p95_s"]
                            * latency,
                            "migrations": DEFAULT_WEIGHTS["migrations"] * migrations,
                        }
                    )
    return weights


def _score(row: dict[str, str], weights: dict[str, float]) -> float:
    """Compute the completion-aware score with explicit weights."""

    return (
        _as_float(row, "completion_ratio")
        - weights["pending"] * _as_float(row, "pending_rate")
        - weights["slo"] * _as_float(row, "slo_violation_rate")
        - weights["latency_p95_s"] * _as_float(row, "latency_p95_s")
        - weights["migrations"] * _as_float(row, "migrations_total")
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into dictionaries."""

    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _as_float(row: dict[str, str], key: str) -> float:
    """Read a numeric field from one grouped-result row."""

    value = row.get(key, "")
    return float(value) if value != "" else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
