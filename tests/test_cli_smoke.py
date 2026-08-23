from pathlib import Path

import pytest


from continuum_bench.runner import load_config
from continuum_bench.realism import detect_evaluation_regime, detect_realism_tier


TRACEBACKED_SMOKE_CONFIGS = [
    ("eo_trace_small.yaml", "space_ground_tracebacked_v1", None, None),
    ("driving_trace_small.yaml", "vehicular_edge_cloud_tracebacked_v1", None, "v2x_edge_iot_energy_v1"),
    (
        "split_inference_trace_small.yaml",
        "edge_cloud_tracebacked_v1",
        "split_inference_batch_lab_v1",
        None,
    ),
    (
        "industrial_iot_trace_small.yaml",
        "edge_cloud_tracebacked_v1",
        "industrial_iot_batch_lab_v1",
        None,
    ),
]


def test_public_smoke_configs_exist() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "configs" / "eo_small.yaml").exists()
    assert (root / "configs" / "driving_small.yaml").exists()
    assert (root / "configs" / "split_inference_small.yaml").exists()
    assert (root / "configs" / "industrial_iot_small.yaml").exists()
    assert (root / "configs" / "eo_trace_small.yaml").exists()
    assert (root / "configs" / "driving_trace_small.yaml").exists()
    assert (root / "configs" / "split_inference_trace_small.yaml").exists()
    assert (root / "configs" / "industrial_iot_trace_small.yaml").exists()


@pytest.mark.parametrize(
    (
        "config_name",
        "expected_network_profile",
        "expected_service_profile_set",
        "expected_energy_profile",
    ),
    TRACEBACKED_SMOKE_CONFIGS,
)
def test_tracebacked_public_configs_declare_expected_calibration_profiles(
    config_name: str,
    expected_network_profile: str,
    expected_service_profile_set: str | None,
    expected_energy_profile: str | None,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / config_name)

    calibration = config["calibration"]

    assert calibration["network_profile"] == expected_network_profile
    if expected_service_profile_set is None:
        assert "service_profile_set" not in calibration
    else:
        assert calibration["service_profile_set"] == expected_service_profile_set
    if expected_energy_profile is None:
        assert "energy_profile" not in calibration
    else:
        assert calibration["energy_profile"] == expected_energy_profile


@pytest.mark.parametrize(
    ("config_name", "expected_mode"),
    [
        ("eo_trace_small.yaml", "trace"),
        ("driving_trace_small.yaml", "trace"),
    ],
)
def test_tracebacked_public_configs_use_contact_trace_files(
    config_name: str,
    expected_mode: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / config_name)

    contact_schedule = config["infrastructure"]["contact_schedule"]

    assert contact_schedule["mode"] == expected_mode
    assert (root / contact_schedule["path"]).exists()


@pytest.mark.parametrize(
    "config_name",
    [
        "split_inference_trace_small.yaml",
        "industrial_iot_trace_small.yaml",
    ],
)
def test_calibrated_public_configs_without_workflow_contact_do_not_use_contact_traces(
    config_name: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / config_name)

    infrastructure = config["infrastructure"]
    evaluation = detect_evaluation_regime(config)

    assert "contact_schedule" not in infrastructure
    assert evaluation["detected_regime"] == "F2"
    assert evaluation["overlays"]["contact_mobility"] is False
    assert evaluation["overlays"]["calibrated_trace_backed"] is True


@pytest.mark.parametrize(
    "config_name",
    [
        "eo_small.yaml",
        "driving_small.yaml",
        "split_inference_small.yaml",
        "industrial_iot_small.yaml",
    ],
)
def test_existing_small_public_configs_leave_calibration_opt_in(config_name: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / config_name)

    assert "calibration" not in config
    assert (root / "configs" / "eo_trace_small.yaml").exists()
    assert (root / "configs" / "driving_trace_small.yaml").exists()
    assert (root / "configs" / "split_inference_trace_small.yaml").exists()
    assert (root / "configs" / "industrial_iot_trace_small.yaml").exists()


@pytest.mark.parametrize(
    ("config_name", "expected_tier", "expected_regime"),
    [
        ("eo_small.yaml", "T0", "F0"),
        ("driving_small.yaml", "T0", "F0"),
        ("split_inference_small.yaml", "T0", "F0"),
        ("industrial_iot_small.yaml", "T0", "F0"),
        ("eo_trace_small.yaml", "T4", "F2"),
        ("driving_trace_small.yaml", "T4", "F2"),
        ("split_inference_trace_small.yaml", "T4", "F2"),
        ("industrial_iot_trace_small.yaml", "T4", "F2"),
    ],
)
def test_public_configs_declare_expected_realism_tier(
    config_name: str,
    expected_tier: str,
    expected_regime: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / config_name)

    realism = detect_realism_tier(config)
    evaluation = detect_evaluation_regime(config)

    assert config["realism_tier"] == expected_tier
    assert realism["declared_tier"] == expected_tier
    assert realism["detected_tier"] == expected_tier
    assert evaluation["detected_regime"] == expected_regime
    assert realism["evaluation_regime"] == expected_regime


def test_split_inference_public_config_avoids_unused_space_contact_structure() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "split_inference_small.yaml")

    infrastructure = config["infrastructure"]

    assert infrastructure.get("n_space", 0) == 0
    assert "contact_schedule" not in infrastructure


def test_industrial_iot_public_config_targets_edge_and_cloud_layers() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "industrial_iot_small.yaml")

    infrastructure = config["infrastructure"]

    assert infrastructure.get("n_iot", 0) >= 1
    assert infrastructure.get("n_edge", 0) >= 1
    assert infrastructure.get("n_cloud", 0) >= 1
    assert infrastructure.get("n_space", 0) == 0


def test_industrial_iot_public_config_uses_moderate_demo_load() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "industrial_iot_small.yaml")

    segments = config["workload"]["streams"]["SensorSource"]["segments"]
    rates = [float(segment["rate"]) for segment in segments]
    scaling = config["scaling"]
    pool_cfg = scaling["pools"]["BulkArchive"]

    assert max(rates) <= 2.5
    assert sum(rates) / len(rates) <= 2.0
    assert int(pool_cfg["initial_workers"]) >= 2
    assert int(pool_cfg["max_workers"]) >= 3
    assert int(scaling["queue_up_threshold"]) <= 8
    assert float(scaling["age_up_threshold_s"]) <= 6.0
