"""Per-class deadlines: the sink's deadline can differ per task class."""

from __future__ import annotations

from continuum_bench.metrics import summarize_epochs
from continuum_bench.runner import ContinuumBenchRunner, Task

from tests.test_runner_integration import _freeze_existing_placement, _runner_config, _stage_id


def _runner_with(tmp_path, deadline_by_priority):
    config = _runner_config(initial_workers=1, max_workers=1)
    config["deadline_by_priority"] = deadline_by_priority
    return ContinuumBenchRunner(config=config, controller_name="separated_loops", seed=0, out_root=tmp_path)


def _enqueue_at_sink(runner, task_id, priority, created_time_s):
    sink_id = _stage_id(runner, "Sink")
    runner.stage_runtimes[sink_id].queue.append(
        Task(task_id=task_id, source_id=sink_id, created_epoch=0, created_time_s=created_time_s,
             priority=priority, size_bytes=32)
    )


def test_same_latency_misses_only_the_class_with_the_tight_deadline(tmp_path, monkeypatch):
    runner = _runner_with(tmp_path, {"high": 1.0, "low": 5.0})
    # Both tasks complete at t=2 s with 2 s latency: high (1 s deadline) misses, low (5 s) is on time; both resolve inside the 8 s horizon.
    _enqueue_at_sink(runner, "h", "high", created_time_s=0.0)
    _enqueue_at_sink(runner, "l", "low", created_time_s=0.0)
    _freeze_existing_placement(runner, monkeypatch, epoch=1)

    record = runner._run_epoch(1)
    per_priority = record["completions"]["per_priority"]
    assert per_priority["high"]["deadline_miss_count"] == 1
    assert per_priority["low"]["deadline_miss_count"] == 0

    final = runner._run_epoch(runner.epochs - 1)
    summary = summarize_epochs(
        {"scenario": runner.scenario.name, "step_seconds": runner.step_seconds, "epochs": runner.epochs},
        [record, final],
    )
    assert summary["qos"]["on_time_rate_by_priority"] == {"high": 0.0, "low": 1.0}


def test_class_without_an_entry_falls_back_to_the_sink_deadline(tmp_path):
    runner = _runner_with(tmp_path, {"high": 1.0})
    sink_profile = runner.stage_runtimes[_stage_id(runner, "Sink")].profile
    sink_profile["deadline_s"] = 7.0
    assert runner._deadline_for("high", sink_profile) == 1.0
    assert runner._deadline_for("low", sink_profile) == 7.0
