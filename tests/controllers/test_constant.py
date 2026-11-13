"""Constant controller: pinned targets, clamping to the pool range and name parsing."""

from __future__ import annotations

from continuum_bench.controllers import WorkerPoolSnapshot
from continuum_bench.controllers.constant import ConstantController, parse_constant_target
from continuum_bench.runner import ContinuumBenchRunner

from tests.test_runner_integration import _runner_config


def _snapshot(active: int, total: int = 6, min_workers: int = 1) -> WorkerPoolSnapshot:
    ids = [f"w{i}" for i in range(total)]
    return WorkerPoolSnapshot(
        stage="BulkPath", min_workers=min_workers, max_workers=total, active_workers=active,
        total_workers=total, active_worker_ids=ids[:active], inactive_worker_ids=ids[active:],
    )


def test_constant_pins_n_and_clamps_to_pool_range():
    assert ConstantController(active_workers=3)._desired_workers(_snapshot(active=1)) == 3
    assert ConstantController(active_workers=99)._desired_workers(_snapshot(active=1)) == 6
    assert ConstantController(active_workers=0)._desired_workers(_snapshot(active=4)) == 1
    assert ConstantController()._desired_workers(_snapshot(active=1)) == 6  # bare constant = max


def test_constant_controller_holds_the_pool_for_the_whole_run(tmp_path):
    runner = ContinuumBenchRunner(
        config=_runner_config(initial_workers=1, max_workers=2),
        controller_name="constant:2",
        seed=0,
        out_root=tmp_path,
    )
    assert isinstance(runner.controller, ConstantController)
    result = runner.run()
    assert result["summary"]["cost"]["mean_active_workers"] == 2.0


def test_constant_per_pool_targets_and_name_parsing():
    assert parse_constant_target("constant") is None
    assert parse_constant_target("constant:3") == 3
    assert parse_constant_target("constant:Fusion=2,BulkPath=6") == {"Fusion": 2, "BulkPath": 6}
    ctrl = ConstantController(active_workers={"Fusion": 2, "BulkPath": 6})
    fusion = WorkerPoolSnapshot(stage="Fusion", min_workers=1, max_workers=4, active_workers=1, total_workers=4,
                                active_worker_ids=["f0"], inactive_worker_ids=["f1", "f2", "f3"])
    assert ctrl._desired_workers(fusion) == 2
    assert ctrl._desired_workers(_snapshot(active=1)) == 6
