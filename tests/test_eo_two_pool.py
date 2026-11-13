"""The two-pool variant: eo_dual_stream with Fusion as a second scalable pool under a shared node budget."""

from __future__ import annotations

import pytest

pytest.importorskip("eclypse")

from continuum_bench.runner import ContinuumBenchRunner, load_config
from continuum_bench.scenarios import build_scenario


def _short(config: dict, epochs: int = 12, drain: int = 4) -> dict:
    config = dict(config)
    config["epochs"], config["drain_epochs"] = epochs, drain
    return config


def test_fusion_pool_is_opt_in_and_balance_routed():
    legacy = build_scenario("eo_dual_stream", config={}, seed=0)
    assert set(legacy.worker_pools) == {"BulkPath"}
    two_pool = build_scenario("eo_dual_stream", config=load_config("configs/eo_two_pool.yaml"), seed=0)
    assert {k: len(v) for k, v in two_pool.worker_pools.items()} == {"BulkPath": 8, "Fusion": 4}
    for source in two_pool.source_stages:
        route = two_pool.route_specs[source]
        assert (route.mode, route.balance_pool) == ("balance", "Fusion")
    for worker in two_pool.worker_pools["Fusion"]:
        assert two_pool.route_specs[worker].mode == "single"
        assert two_pool.route_specs[worker].target.endswith("_Triage")


def test_constant_max_is_infeasible_under_the_shared_budget_and_the_run_survives(tmp_path):
    config = _short(load_config("configs/eo_two_pool.yaml"))
    runner = ContinuumBenchRunner(config=config, controller_name="constant:Fusion=4,BulkPath=8", seed=0, out_root=tmp_path)
    result = runner.run()
    active = result["summary"]["cost"]["mean_active_workers"]
    assert active < 12  # both pools at their maximum (12 workers) never fit; a smaller pool was held


def test_feasible_constant_allocation_runs_both_pools(tmp_path):
    config = _short(load_config("configs/eo_two_pool.yaml"))
    runner = ContinuumBenchRunner(config=config, controller_name="constant:Fusion=2,BulkPath=4", seed=0, out_root=tmp_path)
    result = runner.run()
    assert result["summary"]["cost"]["mean_active_workers"] == pytest.approx(6.0)
    assert set(result["summary"]["qos"]["on_time_rate_by_priority"]) <= {"high", "low"}
