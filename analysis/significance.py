#!/usr/bin/env python3
"""Paired significance tests for the main controller comparison.

An additional check, not reported in the ContinuumBench paper.

For each scenario/regime cell of the main comparison, this pairs the selected scale-capable
controller (HPA) against the matched placement-only controller per seed and runs a one-sided paired
Wilcoxon signed-rank test on completion (H1: scale-capable >= placement-only) and SLO violation
(H1: scale-capable <= placement-only). It reports per-cell p-values and how many of the eight cells
are significant at p<0.05.

Usage:
    python analysis/significance.py --aggregate-csv runs/main/analysis/aggregate.csv \
        [--out runs/main/analysis/significance.json] [--alpha 0.05]
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from scipy.stats import wilcoxon

# Scale-capable controller and the matched placement-only controller per (scenario, regime), as in
# Table 3 of the paper.
SCALE_CAPABLE = "hpa"
MATCHED_PLACEMENT = {
    ("eo_dual_stream", "F0"): "placement_only:milp",
    ("eo_dual_stream", "F2"): "best_fit",
    ("driving_v2x", "F0"): "placement_only:cost_greedy",
    ("driving_v2x", "F2"): "placement_only:cost_greedy",
    ("split_inference", "F0"): "best_fit",
    ("split_inference", "F2"): "best_fit",
    ("industrial_iot", "F0"): "placement_only:cost_greedy",
    ("industrial_iot", "F2"): "placement_only:cost_greedy",
}
# realism_tier -> regime label used above.
TIER_TO_REGIME = {"T0": "F0", "T4": "F2"}


def _regime(row: dict) -> str:
    """Return the F0/F2 regime label for an aggregate-csv row."""

    return TIER_TO_REGIME.get(row.get("realism_tier", ""), row.get("evaluation_regime", ""))


def _load(aggregate_csv: Path) -> dict:
    """Index per-seed (completion, slo) by (scenario, regime, controller)."""

    index: dict = defaultdict(dict)
    with aggregate_csv.open() as handle:
        for row in csv.DictReader(handle):
            key = (row["scenario"], _regime(row), row["controller"])
            index[key][int(row["seed"])] = (
                float(row["completion_ratio"]),
                float(row["slo_violation_rate"]),
            )
    return index


def _paired(values_a: list[float], values_b: list[float], alternative: str) -> float:
    """Return the one-sided paired Wilcoxon p-value, or nan if all paired differences are zero."""

    if all(abs(a - b) < 1e-12 for a, b in zip(values_a, values_b)):
        return float("nan")
    return float(wilcoxon(values_a, values_b, alternative=alternative, zero_method="wilcox").pvalue)


def run(aggregate_csv: Path, alpha: float) -> dict:
    """Compute the per-cell paired Wilcoxon tests and significance counts."""

    index = _load(aggregate_csv)
    cells = []
    sig_completion = sig_slo = 0
    for (scenario, regime), placement in MATCHED_PLACEMENT.items():
        sc = index.get((scenario, regime, SCALE_CAPABLE), {})
        pl = index.get((scenario, regime, placement), {})
        seeds = sorted(set(sc) & set(pl))
        sc_comp = [sc[s][0] for s in seeds]
        pl_comp = [pl[s][0] for s in seeds]
        sc_slo = [sc[s][1] for s in seeds]
        pl_slo = [pl[s][1] for s in seeds]
        p_completion = _paired(sc_comp, pl_comp, "greater")
        p_slo = _paired(sc_slo, pl_slo, "less")
        if p_completion == p_completion and p_completion < alpha:  # not nan and significant
            sig_completion += 1
        if p_slo == p_slo and p_slo < alpha:
            sig_slo += 1
        cells.append(
            {
                "scenario": scenario,
                "regime": regime,
                "scale_capable": SCALE_CAPABLE,
                "placement_only": placement,
                "n_seeds": len(seeds),
                "p_completion_greater": p_completion,
                "p_slo_less": p_slo,
            }
        )
    return {
        "alpha": alpha,
        "n_cells": len(cells),
        "completion_significant": sig_completion,
        "slo_significant": sig_slo,
        "cells": cells,
    }


def main() -> None:
    """CLI entry point."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aggregate-csv", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--alpha", type=float, default=0.05)
    args = parser.parse_args()

    result = run(args.aggregate_csv, args.alpha)
    print(
        f"Paired Wilcoxon (one-sided, {result['cells'][0]['n_seeds']} seeds): "
        f"SLO reduction significant in {result['slo_significant']}/{result['n_cells']} cells; "
        f"completion improvement significant in "
        f"{result['completion_significant']}/{result['n_cells']} cells (alpha={args.alpha})."
    )
    for cell in result["cells"]:
        print(
            f"  {cell['scenario']:<16} {cell['regime']}  "
            f"completion p={cell['p_completion_greater']:.4g}  slo p={cell['p_slo_less']:.4g}"
        )
    if args.out is not None:
        args.out.write_text(json.dumps(result, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
