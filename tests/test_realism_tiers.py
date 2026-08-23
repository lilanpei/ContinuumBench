from __future__ import annotations

from continuum_bench.realism import detect_evaluation_regime, detect_realism_tier
from continuum_bench.runner import ContinuumBenchRunner, load_config


def test_detect_realism_tiers_from_config_features() -> None:
    assert detect_realism_tier({}) == {
        "declared_tier": None,
        "detected_tier": "T0",
        "label": "base_deterministic",
        "features": {
            "energy_aware": False,
            "failure_aware": False,
            "mobility_contact_trace": False,
            "calibrated_trace_backed": False,
        },
        "evaluation_regime": "F0",
        "evaluation_label": "deterministic_synthetic",
    }

    assert detect_realism_tier(
        {"calibration": {"energy_profile": "v2x_edge_iot_energy_v1"}}
    )["detected_tier"] == "T1"
    assert detect_realism_tier(
        {"reliability": {"failures": {"mode": "bernoulli"}}}
    )["detected_tier"] == "T2"
    assert detect_realism_tier(
        {"infrastructure": {"contact_schedule": {"mode": "trace", "path": "trace.csv"}}}
    )["detected_tier"] == "T3"
    assert detect_realism_tier(
        {"calibration": {"network_profile": "edge_cloud_tracebacked_v1"}}
    )["detected_tier"] == "T4"
    assert detect_realism_tier(
        {
            "infrastructure": {"contact_schedule": {"mode": "trace", "path": "trace.csv"}},
            "calibration": {
                "network_profile": "vehicular_edge_cloud_tracebacked_v1",
                "energy_profile": "v2x_edge_iot_energy_v1",
            },
        }
    )["detected_tier"] == "T4"


def test_detect_evaluation_regimes_from_config_features() -> None:
    base = detect_evaluation_regime({})
    assert base["declared_regime"] is None
    assert base["detected_regime"] == "F0"
    assert base["label"] == "deterministic_synthetic"
    assert base["overlays"] == {
        "energy": False,
        "failures": False,
        "contact_mobility": False,
        "calibrated_profiles": False,
        "calibrated_trace_backed": False,
    }

    energy = detect_evaluation_regime(
        {"calibration": {"energy_profile": "v2x_edge_iot_energy_v1"}}
    )
    assert energy["detected_regime"] == "F0"
    assert energy["overlays"]["energy"] is True
    assert energy["overlays"]["calibrated_profiles"] is False

    contact = detect_evaluation_regime(
        {"infrastructure": {"contact_schedule": {"mode": "trace", "path": "trace.csv"}}}
    )
    assert contact["detected_regime"] == "F1"
    assert contact["overlays"]["contact_mobility"] is True

    calibrated = detect_evaluation_regime(
        {"calibration": {"network_profile": "edge_cloud_tracebacked_v1"}}
    )
    assert calibrated["detected_regime"] == "F2"
    assert calibrated["overlays"]["contact_mobility"] is False
    assert calibrated["overlays"]["calibrated_profiles"] is True
    assert calibrated["overlays"]["calibrated_trace_backed"] is True

    trace_backed = detect_evaluation_regime(
        {
            "infrastructure": {"contact_schedule": {"mode": "trace", "path": "trace.csv"}},
            "calibration": {"network_profile": "vehicular_edge_cloud_tracebacked_v1"},
        }
    )
    assert trace_backed["detected_regime"] == "F2"
    assert trace_backed["overlays"]["calibrated_trace_backed"] is True


def test_runner_metadata_records_declared_and_detected_realism_tiers(tmp_path) -> None:
    config = {
        "scenario": "eo_dual_stream",
        "epochs": 1,
        "realism_tier": "T1",
        "infrastructure": {
            "n_space": 1,
            "n_iot": 1,
            "n_edge": 1,
            "n_cloud": 1,
            "contact_schedule": {
                "mode": "periodic",
                "period_s": 10,
                "on_duration_s": 5,
            },
        },
        "workload": {
            "streams": {
                "SpaceSource": {"generator": "piecewise", "segments": [{"epochs": 1, "rate": 0.0}]},
                "IoTSource": {"generator": "piecewise", "segments": [{"epochs": 1, "rate": 0.0}]},
            }
        },
        "scaling": {"pools": {"BulkPath": {"initial_workers": 0, "max_workers": 1}}},
        "calibration": {"energy_profile": "v2x_edge_iot_energy_v1"},
    }

    result = ContinuumBenchRunner(
        config=config,
        controller_name="no_scaling",
        seed=0,
        out_root=tmp_path,
    ).run()

    realism = result["summary"]["metadata"]["realism"]
    evaluation = result["summary"]["metadata"]["evaluation"]
    assert evaluation["declared_regime"] == "F0"
    assert evaluation["detected_regime"] == "F0"
    assert evaluation["overlays"]["energy"] is True
    assert evaluation["overlays"]["calibrated_profiles"] is False
    assert realism["declared_tier"] == "T1"
    assert realism["detected_tier"] == "T1"
    assert realism["features"]["energy_aware"] is True
    assert realism["evaluation_regime"] == "F0"


def test_trace_config_declares_calibrated_trace_backed_tier() -> None:
    config = load_config("configs/driving_trace_small.yaml")

    realism = detect_realism_tier(config)

    assert realism["declared_tier"] == "T4"
    assert realism["detected_tier"] == "T4"
    assert realism["evaluation_regime"] == "F2"
    assert realism["features"] == {
        "energy_aware": True,
        "failure_aware": False,
        "mobility_contact_trace": True,
        "calibrated_trace_backed": True,
    }


def test_calibrated_trace_config_can_be_f2_without_contact_overlay() -> None:
    config = load_config("configs/split_inference_trace_small.yaml")

    realism = detect_realism_tier(config)

    assert realism["declared_tier"] == "T4"
    assert realism["detected_tier"] == "T4"
    assert realism["evaluation_regime"] == "F2"
    assert realism["features"] == {
        "energy_aware": False,
        "failure_aware": False,
        "mobility_contact_trace": False,
        "calibrated_trace_backed": True,
    }
