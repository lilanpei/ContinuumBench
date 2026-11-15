"""Golden regression test pinning the paper's headline numbers.

This guards the paper's Table 3 (the main comparison) and its score-robustness check, so that a
change to ``summarize_epochs``, the SLO classifier, the selection score, or any controller cannot
silently shift the reported values. The frozen snapshot lives in
``tests/golden/main_headline.json``.

When the (git-ignored) ``runs/main`` analysis artifacts are present locally -- i.e. after
re-running ``sweeps/main.yaml`` plus the analysis pipeline -- this test recomputes the
headline values and asserts they match the snapshot within tolerance. On a bare clone without the
run tree it skips. If a sweep or code change legitimately changes the numbers, regenerate the
snapshot from ``runs/main/analysis``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads((Path(__file__).parent / "golden" / "main_headline.json").read_text())
GROUPED = ROOT / "runs" / "main" / "analysis" / "grouped_strict.csv"
SCORE = ROOT / "runs" / "main" / "analysis" / "score_sensitivity.json"

requires_runs = pytest.mark.skipif(
    not (GROUPED.exists() and SCORE.exists()),
    reason="main run artifacts not present (git-ignored); re-run sweeps to enable this regression check",
)


@requires_runs
def test_table3_cells_match_golden() -> None:
    """Every cell behind Table 3 (scenario, regime, controller) matches the frozen snapshot."""

    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    with GROUPED.open() as handle:
        for row in csv.DictReader(handle):
            rows[(row["scenario"], row.get("realism_tier", ""), row["controller"])] = row

    for cell in GOLDEN["table3_cells"]:
        key = (cell["scenario"], cell["realism_tier"], cell["controller"])
        assert key in rows, f"missing group {key} in grouped_strict.csv"
        row = rows[key]
        assert float(row["completion_ratio"]) == pytest.approx(cell["completion_ratio"], abs=0.005), key
        assert float(row["slo_violation_rate"]) == pytest.approx(cell["slo_violation_rate"], abs=0.005), key
        assert float(row["latency_p95_s"]) == pytest.approx(cell["latency_p95_s"], abs=0.2), key


@requires_runs
def test_score_robustness_invariants_match_golden() -> None:
    """Score robustness: 1 of 8 contexts changes over the 81-point grid; SLO reduction about 0.14."""

    score = json.loads(SCORE.read_text())
    golden = GOLDEN["score_sensitivity"]
    assert score["contexts_with_controller_choice_changes"] == golden["contexts_with_controller_choice_changes"]
    assert score["total_contexts"] == 8
    assert score["weight_grid_size"] == 81
    assert score["min_slo_reduction_placement_minus_scale"] == pytest.approx(golden["min_slo_reduction"], abs=1e-3)
    assert score["min_completion_delta_scale_minus_placement"] == pytest.approx(golden["min_completion_delta"], abs=1e-3)
