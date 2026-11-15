"""Unit tests for analysis/findings.py on small synthetic run directories (no simulation)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_findings():
    spec = importlib.util.spec_from_file_location("findings", ROOT / "analysis" / "findings.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["findings"] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


findings = _load_findings()


def _write_run(
    root: Path,
    index: int,
    *,
    scenario="eo_dual_stream",
    controller="hpa",
    seed=0,
    tier="T0",
    completion=0.5,
    pending=0.5,
    slo=0.4,
    late=0.2,
    eligible=10,
    violations=4,
    generated=20,
    migrations=0.0,
    decision_ms=1.0,
    config=None,
    sweep=None,
):
    run_dir = root / f"run{index:03d}"
    (run_dir / "provenance").mkdir(parents=True)
    summary = {
        "metadata": {
            "scenario": scenario,
            "controller": controller,
            "seed": seed,
            "step_seconds": 1.0,
            "realism": {"detected_tier": tier},
        },
        "totals": {
            "completion_ratio": completion,
            "pending_rate": pending,
            "generated_tasks": generated,
        },
        "qos": {
            "slo_violation_rate": slo,
            "deadline_miss_rate": late,
            "slo_eligible_count": eligible,
            "slo_violation_count": violations,
        },
        "latency_s": {"p95": 10.0},
        "stability": {"migrations_total": migrations},
        "controller_overhead_ms": {"p95": decision_ms},
    }
    (run_dir / "summary.json").write_text(json.dumps(summary))
    (run_dir / "provenance" / "config.yaml").write_text(
        yaml.safe_dump(config or {"scenario": scenario})
    )
    if sweep is not None:
        (run_dir / "provenance" / "sweep.json").write_text(json.dumps(sweep))
    return run_dir


def test_on_time_delivery_uses_the_runner_accounting(tmp_path):
    _write_run(tmp_path, 0, eligible=10, violations=4, generated=20)
    (run,) = findings.load_runs(tmp_path)
    assert run.on_time == pytest.approx(6 / 20)


def test_staging_configs_are_not_runs(tmp_path):
    _write_run(tmp_path, 0)
    staging = tmp_path / ".sweep_staging" / "x"
    staging.mkdir(parents=True)
    (staging / "summary.json").write_text("{}")
    assert len(findings.load_runs(tmp_path)) == 1


def test_replan_schedules_with_equal_completion_differ_by_zero(tmp_path):
    index = 0
    for on_scale, periodic in ((True, True), (False, True), (False, False)):
        for seed in (0, 1):
            config = {
                "scenario": "eo_dual_stream",
                "infrastructure": {"n_edge": 2, "n_cloud": 1},
                "placement": {
                    "strategy": "min_latency",
                    "replan_on_scale": on_scale,
                    "periodic_replan": periodic,
                },
            }
            _write_run(tmp_path, index, seed=seed, completion=0.5 + 0.1 * seed, config=config)
            index += 1
    result = findings.replan_summary(findings.load_runs(tmp_path))
    assert result["configurations"] == 1
    assert result["schedules"] == ["coupled", "fixed", "periodic"]
    assert result["schedule_difference"] == pytest.approx(0.0)
    assert result["migrations"] == 0


def test_placement_spread_and_arms(tmp_path):
    for index, (strategy, violations) in enumerate((("min_latency", 2), ("cost_greedy", 9))):
        _write_run(
            tmp_path,
            index,
            controller=f"placement_only:{strategy}",
            violations=violations,
            eligible=10,
            generated=10,
            sweep={"run": f"stress_{strategy}"},
        )
    summary = findings.placement_summary(findings.load_runs(tmp_path))
    assert set(summary) == {"stress"}
    assert summary["stress"]["table"]["min_latency"][1] == pytest.approx(0.8)
    assert summary["stress"]["spread"] == pytest.approx(0.7)


def test_failure_inversion_is_counted_per_configuration(tmp_path):
    rows = [
        # controller, p, completed-only rate, completion-aware rate
        ("hpa", 0.0, 0.30, 0.40),
        ("hpa", 0.08, 0.20, 0.50),  # inversion only over completed tasks
        ("keda", 0.0, 0.30, 0.40),
        ("keda", 0.08, 0.35, 0.60),  # no inversion
    ]
    for index, (controller, p, late, slo) in enumerate(rows):
        failures = (
            {"mode": "bernoulli", "node_failure_probability": p} if p else {"mode": "disabled"}
        )
        config = {"scenario": "eo_dual_stream", "reliability": {"failures": failures}}
        _write_run(tmp_path, index, controller=controller, late=late, slo=slo, config=config)
    result = findings.failure_summary(findings.load_runs(tmp_path))
    assert [r[1] for r in result["completed_only_falls"]] == ["hpa"]
    assert result["aware_falls"] == []


def test_selection_reports_exact_ties(tmp_path):
    for index, controller in enumerate(("placement_only:cost_greedy", "best_fit")):
        _write_run(tmp_path, index, controller=controller)
    runs = findings.load_runs(tmp_path)
    best, ties = findings.select(
        findings.group_by(runs, lambda r: r.controller), findings.PLACEMENT_ONLY
    )
    assert best == "best_fit"
    assert ties == ["placement_only:cost_greedy"]


def test_rank_changes_between_lightest_and_heaviest_contact(tmp_path):
    scores = {
        "mild": {"hpa": 0.9, "keda": 0.5},
        "base": {"hpa": 0.9, "keda": 0.5},
        "stress": {"hpa": 0.4, "keda": 0.6},
    }
    index = 0
    for level, by_controller in scores.items():
        path = "traces/eo.csv" if level == "base" else f"traces/eo_{level}.csv"
        for controller, completion in by_controller.items():
            config = {
                "scenario": "eo_dual_stream",
                "infrastructure": {"contact_schedule": {"mode": "trace", "path": path}},
            }
            _write_run(tmp_path, index, controller=controller, completion=completion, config=config)
            index += 1
    changes = findings.rank_changes(
        findings.load_runs(tmp_path), findings.contact_level, findings.CONTACT_LEVELS.index
    )
    assert changes["eo_dual_stream"][:2] == (2, 2)


def test_arrival_rate_is_the_time_weighted_mean():
    config = {
        "workload": {
            "streams": {
                "A": {"segments": [{"epochs": 10, "rate": 1.0}, {"epochs": 30, "rate": 3.0}]}
            }
        }
    }
    assert findings.arrival_rate(config) == pytest.approx(2.5)


def _write_sweep(root: Path, entries: list[tuple[str, int]]) -> None:
    for index, (run, seed) in enumerate(entries):
        _write_run(root, index, seed=seed, sweep={"sweep": "demo", "run": run, "seed": seed})


def _write_manifest(directory: Path, runs: list[str], seeds: list[int]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "demo",
        "seeds": seeds,
        "runs": [{"name": r, "controller": "hpa"} for r in runs],
    }
    (directory / "demo.yaml").write_text(yaml.safe_dump(manifest))
    return directory


def test_complete_sweep_passes(tmp_path):
    manifests = _write_manifest(tmp_path / "sweeps", ["a", "b"], [0, 1])
    _write_sweep(tmp_path / "runs", [("a", 0), ("a", 1), ("b", 0), ("b", 1)])
    runs = findings.load_runs(tmp_path / "runs")
    findings.check_complete(runs, tmp_path / "runs", manifest_dir=manifests)


def test_a_run_missing_from_the_manifest_is_reported(tmp_path):
    manifests = _write_manifest(tmp_path / "sweeps", ["a", "b"], [0, 1])
    _write_sweep(tmp_path / "runs", [("a", 0), ("a", 1)])
    runs = findings.load_runs(tmp_path / "runs")
    with pytest.raises(SystemExit, match="run b, seed 0 is missing"):
        findings.check_complete(runs, tmp_path / "runs", manifest_dir=manifests)


def test_a_missing_seed_and_a_repeated_run_are_reported(tmp_path):
    _write_sweep(tmp_path, [("a", 0), ("a", 1), ("b", 0), ("b", 0)])
    with pytest.raises(
        SystemExit, match="run b, seed 0 appears more than once; run b, seed 1 is missing"
    ):
        findings.load_runs(tmp_path)
