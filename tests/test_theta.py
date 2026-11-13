"""The knob vector theta: schema, validation and projection onto the controller's configuration."""

from __future__ import annotations

import math

import pytest

from continuum_bench.theta import ThetaError, apply_theta, default_theta, load_schema, validate_theta


def test_default_theta_follows_schema_key_order_and_defaults():
    schema = load_schema()
    theta = default_theta(schema)
    assert list(theta) == list(schema["knobs"])
    assert theta["w_cost"] == 0.0 and theta["worker_cap"] == 8 and theta["residency"] == "any"
    assert math.isinf(theta["egress_budget_mb"])


def test_validate_fills_missing_knobs_and_rejects_out_of_level_or_unknown():
    theta = validate_theta({"w_cost": 1.0})
    assert theta["w_cost"] == 1.0 and theta["prewarm_lead_s"] == 10.0
    with pytest.raises(ThetaError):
        validate_theta({"w_cost": 0.3})
    with pytest.raises(ThetaError):
        validate_theta({"residency": "mars_only"})
    with pytest.raises(ThetaError):
        validate_theta({"not_a_knob": 1})
    with pytest.raises(ThetaError):
        validate_theta({"worker_cap": 2.5})


def test_apply_theta_projects_every_consumer_block_and_keeps_input_untouched():
    config = {
        "scaling": {"pools": {"BulkPath": {"min_workers": 1, "initial_workers": 4, "max_workers": 8}}},
        "autoscaling": {"anticipatory": {"period_s": 120.0}},
        "placement": {"strategy": "milp"},
    }
    theta = {
        "w_cost": 0.5, "worker_cap": 2, "prewarm_lead_s": 4, "drain_s": 5, "prewarm_workers": 2,
        "scale_up_util": 0.9, "deadline_high_s": 10, "deadline_low_s": 100,
        "tier_price_edge": 4.0, "residency": "no_cloud", "egress_budget_mb": 1, "energy_cap_w": 120,
    }
    out = apply_theta(config, theta)

    assert config["scaling"]["pools"]["BulkPath"]["max_workers"] == 8  # input not mutated
    pool = out["scaling"]["pools"]["BulkPath"]
    assert pool == {"min_workers": 1, "initial_workers": 2, "max_workers": 2}  # clamped to the cap
    ant = out["autoscaling"]["anticipatory"]
    assert ant["period_s"] == 120.0  # untouched
    assert (ant["lead_s"], ant["drain_s"], ant["prewarm_workers"], ant["target_utilization"]) == (4.0, 5.0, 2, 0.9)
    assert out["deadline_by_priority"] == {"high": 10.0, "low": 100.0}
    milp = out["placement"]["milp"]
    assert milp["weights"] == {"latency": 1.0, "cost": 0.5, "energy": 0.0}
    assert milp["tier_prices"]["edge"] == 4.0 and milp["tier_prices"]["cloud"] == 1.0
    assert milp["residency"] == "no_cloud"
    assert (milp["egress_budget_mb"], milp["energy_cap_w"]) == (1.0, 120.0)
    assert "price_per_tier" not in out.get("cost", {})  # price weights steer the MILP only
    assert out["score"]["weights"]["cost"] == 0.5
    assert out["theta"]["w_cost"] == 0.5 and out["theta"]["w_energy"] == 0.0


def test_apply_theta_cost_scale_override_is_per_run_and_defaults_to_the_constant():
    from continuum_bench.theta import MILP_COST_SCALE_MS

    config = {"placement": {"strategy": "milp"}}
    assert apply_theta(config, {})["placement"]["milp"]["cost_scale"] == MILP_COST_SCALE_MS
    overridden = apply_theta({**config, "theta_options": {"milp_cost_scale_ms": 40}}, {})
    assert overridden["placement"]["milp"]["cost_scale"] == 40.0


def test_floor_knobs_raise_pool_minimums_up_to_the_cap_and_never_below_the_scenario():
    config = {"scaling": {"pools": {"Fusion": {"min_workers": 1, "initial_workers": 1, "max_workers": 4},
                                    "BulkPath": {"min_workers": 2, "initial_workers": 2, "max_workers": 8}}}}
    out = apply_theta(config, {"floor_fusion": 3, "floor_bulk": 6, "worker_cap": 4})
    assert out["scaling"]["pools"]["Fusion"] == {"min_workers": 3, "initial_workers": 1, "max_workers": 4}   # initial workers untouched (cold start is common to every policy)
    assert out["scaling"]["pools"]["BulkPath"] == {"min_workers": 4, "initial_workers": 2, "max_workers": 4}  # floor 6 clipped to the cap
    out = apply_theta(config, {"floor_bulk": 1})
    assert out["scaling"]["pools"]["BulkPath"]["min_workers"] == 2  # the scenario's own minimum wins
    assert out["scaling"]["pools"]["Fusion"]["min_workers"] == 1
    assert default_theta()["floor_fusion"] == 1 and default_theta()["floor_bulk"] == 1
