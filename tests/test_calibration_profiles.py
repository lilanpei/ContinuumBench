from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import tomllib
import yaml

import pytest

from continuum_bench.infra.generators import build_extended_continuum
from continuum_bench.runner import load_config


MEASURED_LAB_SERVICE_PROFILE_SETS = {
    "split_inference_measured_lab_v1": {
        "EdgePreprocess": "edge",
        "EdgeInference": "edge",
        "CloudRefine": "cloud",
    },
    "split_inference_batch_lab_v1": {
        "EdgePreprocess": "edge",
        "EdgeInference": "edge",
        "CloudRefine": "cloud",
    },
    "industrial_iot_measured_lab_v1": {
        "Filter": "edge",
        "Aggregate": "edge",
        "AnomalyDetect": "edge",
        "BulkArchive": "cloud",
    },
    "industrial_iot_batch_lab_v1": {
        "Filter": "edge",
        "Aggregate": "edge",
        "AnomalyDetect": "edge",
        "ControlDispatch": "edge",
        "BulkArchive": "cloud",
    },
}


TRACEBACKED_CONFIG_PROFILE_SELECTIONS = [
    ("eo_trace_small.yaml", "space_ground_tracebacked_v1", None),
    ("driving_trace_small.yaml", "vehicular_edge_cloud_tracebacked_v1", None),
    (
        "split_inference_trace_small.yaml",
        "edge_cloud_tracebacked_v1",
        "split_inference_batch_lab_v1",
    ),
    (
        "industrial_iot_trace_small.yaml",
        "edge_cloud_tracebacked_v1",
        "industrial_iot_batch_lab_v1",
    ),
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_modules():
    fit_profiles = importlib.import_module("calibration.fit_profiles")
    validate_rankings = importlib.import_module("calibration.validate_rankings")
    return fit_profiles, validate_rankings


def _load_yaml_file(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _link_profile(bundle, src: str, dst: str) -> dict[str, float]:
    infrastructure = bundle.infrastructure
    if hasattr(infrastructure, "path_resources"):
        return dict(infrastructure.path_resources(src, dst))
    return dict(getattr(infrastructure, "edges")[(src, dst)])


def test_calibration_profiles_load_from_default_files() -> None:
    fit_profiles, _ = _load_modules()

    network_profiles = fit_profiles.load_network_profiles()
    service_profiles = fit_profiles.load_service_time_profiles()

    assert network_profiles["schema_version"] == 1
    assert network_profiles["kind"] == "network_profiles"
    assert set(network_profiles["profiles"]) >= {"iot_edge_stable", "edge_cloud_wan"}

    assert service_profiles["schema_version"] == 1
    assert service_profiles["kind"] == "service_time_profiles"
    assert "split_inference" in service_profiles["profiles"]


def test_build_extended_continuum_applies_selected_network_profile() -> None:
    bundle = build_extended_continuum(
        {
            "n_iot": 1,
            "n_edge": 1,
            "n_cloud": 1,
            "n_space": 0,
            "calibration": {"network_profile": "edge_cloud_wan"},
        },
        seed=7,
    )

    edge_cloud = _link_profile(bundle, "Edge_0", "Cloud_0")

    assert edge_cloud["latency"] == pytest.approx(42.0)
    assert edge_cloud["bandwidth"] == pytest.approx(220.0)


def test_build_extended_continuum_link_override_wins_over_network_calibration() -> None:
    bundle = build_extended_continuum(
        {
            "n_iot": 1,
            "n_edge": 1,
            "n_cloud": 1,
            "n_space": 0,
            "edge_to_cloud": {"latency": 9.0, "bandwidth": 777.0},
            "calibration": {"network_profile": "edge_cloud_wan"},
        },
        seed=7,
    )

    edge_cloud = _link_profile(bundle, "Edge_0", "Cloud_0")

    assert edge_cloud["latency"] == pytest.approx(9.0)
    assert edge_cloud["bandwidth"] == pytest.approx(777.0)


def test_build_extended_continuum_keeps_default_links_without_calibration_config() -> None:
    bundle = build_extended_continuum(
        {
            "n_iot": 1,
            "n_edge": 1,
            "n_cloud": 1,
            "n_space": 0,
        },
        seed=7,
    )

    edge_cloud = _link_profile(bundle, "Edge_0", "Cloud_0")
    iot_edge = _link_profile(bundle, "IoT_0", "Edge_0")

    assert edge_cloud["latency"] == pytest.approx(20.0)
    assert edge_cloud["bandwidth"] == pytest.approx(1000.0)
    assert iot_edge["latency"] == pytest.approx(6.0)
    assert iot_edge["bandwidth"] == pytest.approx(120.0)


def test_build_extended_continuum_applies_selected_energy_profile() -> None:
    bundle = build_extended_continuum(
        {
            "n_iot": 1,
            "n_edge": 1,
            "n_cloud": 1,
            "n_space": 0,
            "calibration": {"energy_profile": "v2x_edge_iot_energy_v1"},
        },
        seed=7,
    )

    infrastructure = bundle.infrastructure
    nodes = infrastructure.nodes
    edges = infrastructure.edges

    assert nodes["IoT_0"]["idle_power_w"] == pytest.approx(1.8)
    assert nodes["IoT_0"]["battery_capacity_j"] == pytest.approx(1_800_000.0)
    assert nodes["Edge_0"]["dynamic_power_w_per_cpu"] == pytest.approx(5.5)
    assert nodes["Cloud_0"]["idle_power_w"] == pytest.approx(180.0)
    assert edges["IoT_0", "Edge_0"]["energy_j_per_byte"] == pytest.approx(2.2e-7)
    assert edges["Edge_0", "Cloud_0"]["energy_j_per_byte"] == pytest.approx(8.0e-8)
    assert "idle_power_w" in infrastructure.node_assets
    assert "dynamic_power_w_per_cpu" in infrastructure.node_assets
    assert "battery_capacity_j" in infrastructure.node_assets
    assert "energy_j_per_byte" in infrastructure.edge_assets


@pytest.mark.parametrize(
    ("calibration_value", "error_match"),
    [
        ("bad", "calibration.*mapping"),
        ({"network_profile": ""}, "network_profile"),
        ({"network_profile": 123}, "network_profile"),
    ],
)
def test_build_extended_continuum_rejects_malformed_calibration_config(
    calibration_value: object,
    error_match: str,
) -> None:
    with pytest.raises(ValueError, match=error_match):
        build_extended_continuum(
            {
                "n_iot": 1,
                "n_edge": 1,
                "n_cloud": 1,
                "n_space": 0,
                "calibration": calibration_value,
            },
            seed=7,
        )


def test_packaged_calibration_helper_is_available_from_package_namespace() -> None:
    continuum_bench = importlib.import_module("continuum_bench")
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")

    helper_path = Path(inspect.getsourcefile(calibration_profiles) or "")
    package_root = Path(continuum_bench.__file__).resolve().parent

    assert helper_path.is_relative_to(package_root)
    assert hasattr(calibration_profiles, "load_selected_network_profile")
    assert hasattr(calibration_profiles, "load_service_profile_set")


def test_packaged_calibration_helper_uses_package_owned_default_assets() -> None:
    continuum_bench = importlib.import_module("continuum_bench")
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")

    package_root = Path(continuum_bench.__file__).resolve().parent
    network_path = Path(getattr(calibration_profiles, "_NETWORK_PROFILES_PATH"))
    service_path = Path(getattr(calibration_profiles, "_SERVICE_TIME_PROFILES_PATH"))

    assert network_path.is_relative_to(package_root)
    assert service_path.is_relative_to(package_root)


def test_packaged_calibration_helper_loads_energy_profiles() -> None:
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")

    energy_profile = calibration_profiles.load_selected_energy_profile(
        {"energy_profile": "v2x_edge_iot_energy_v1"}
    )

    assert energy_profile is not None
    assert energy_profile["source"] != "placeholder"
    assert energy_profile["node_tiers"]["iot"]["idle_power_w"] == pytest.approx(1.8)
    assert energy_profile["link_tiers"]["iot_edge"]["energy_j_per_byte"] == pytest.approx(2.2e-7)


def test_packaged_calibration_assets_are_declared_in_packaging_metadata() -> None:
    pyproject = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    continuum_bench = importlib.import_module("continuum_bench")

    package_data = pyproject.get("tool", {}).get("setuptools", {}).get("package-data", {})
    declared_patterns = package_data.get("continuum_bench", [])

    assert "calibration/*.yaml" in declared_patterns
    assert "calibration/contact_traces/*.csv" in declared_patterns

    package_root = Path(continuum_bench.__file__).resolve().parent
    assert (package_root / "calibration" / "network_profiles.yaml").is_file()
    assert (package_root / "calibration" / "service_time_profiles.yaml").is_file()
    assert (
        package_root / "calibration" / "contact_traces" / "eo_space_ground_small.csv"
    ).is_file()


def test_packaged_calibration_assets_include_explicit_emulated_profile_set() -> None:
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")

    network_profile = calibration_profiles.load_selected_network_profile(
        {"network_profile": "edge_cloud_emulated_v1"}
    )
    service_profiles = calibration_profiles.load_service_profile_set(
        {"service_profile_set": "split_inference_emulated_v1"}
    )

    assert network_profile is not None
    assert network_profile["source"] != "placeholder"
    assert "emulated" in network_profile["source"]
    assert service_profiles is not None
    assert service_profiles["encoder"]["edge"]["source"] != "placeholder"
    assert "emulated" in service_profiles["encoder"]["edge"]["source"]


def test_packaged_calibration_assets_validate_and_include_measured_lab_service_profile_sets() -> None:
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")
    fit_profiles, _ = _load_modules()

    service_path = Path(getattr(calibration_profiles, "_SERVICE_TIME_PROFILES_PATH"))
    validated_service = fit_profiles.validate_service_time_profiles(_load_yaml_file(service_path))

    assert set(validated_service["profiles"]) >= set(MEASURED_LAB_SERVICE_PROFILE_SETS)


@pytest.mark.parametrize(
    ("profile_set_name", "expected_stage_node_classes"),
    list(MEASURED_LAB_SERVICE_PROFILE_SETS.items()),
)
def test_packaged_measured_lab_service_profile_sets_use_measured_lab_provenance(
    profile_set_name: str,
    expected_stage_node_classes: dict[str, str],
) -> None:
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")

    service_profiles = calibration_profiles.load_service_profile_set(
        {"service_profile_set": profile_set_name}
    )

    assert service_profiles is not None
    assert set(service_profiles) >= set(expected_stage_node_classes)

    for stage_name, node_class in expected_stage_node_classes.items():
        stage_profiles = service_profiles[stage_name]
        profile = stage_profiles[node_class]

        assert profile["source"] != "placeholder"
        assert profile["source"].startswith("measured_lab_")


def test_packaged_calibration_assets_include_tracebacked_network_profiles() -> None:
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")
    fit_profiles, _ = _load_modules()

    network_path = Path(getattr(calibration_profiles, "_NETWORK_PROFILES_PATH"))
    validated_network = fit_profiles.validate_network_profiles(_load_yaml_file(network_path))

    expected_profiles = {
        "edge_cloud_tracebacked_v1",
        "vehicular_edge_cloud_tracebacked_v1",
        "space_ground_tracebacked_v1",
    }

    assert expected_profiles <= set(validated_network["profiles"])

    for profile_name in sorted(expected_profiles):
        selected_profile = calibration_profiles.load_selected_network_profile(
            {"network_profile": profile_name}
        )

        assert selected_profile is not None
        assert selected_profile["source"] != "placeholder"
        assert selected_profile["source"].startswith("public_tracebacked_")

    assert "public_tracebacked_ripe_atlas_mlab_v1" == calibration_profiles.load_selected_network_profile(
        {"network_profile": "edge_cloud_tracebacked_v1"}
    )["source"]
    assert "public_tracebacked_lust_ripe_atlas_mlab_v1" == calibration_profiles.load_selected_network_profile(
        {"network_profile": "vehicular_edge_cloud_tracebacked_v1"}
    )["source"]
    assert "public_tracebacked_celestrak_hypatia_v1" == calibration_profiles.load_selected_network_profile(
        {"network_profile": "space_ground_tracebacked_v1"}
    )["source"]


@pytest.mark.parametrize(
    ("config_name", "expected_network_profile", "expected_service_profile_set"),
    TRACEBACKED_CONFIG_PROFILE_SELECTIONS,
)
def test_tracebacked_smoke_configs_reference_packaged_calibration_profiles(
    config_name: str,
    expected_network_profile: str,
    expected_service_profile_set: str | None,
) -> None:
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")
    config = load_config(_repo_root() / "configs" / config_name)

    calibration_cfg = config["calibration"]
    selected_network = calibration_profiles.load_selected_network_profile(calibration_cfg)

    assert calibration_cfg["network_profile"] == expected_network_profile
    assert selected_network is not None

    selected_service_profile_set = calibration_profiles.load_service_profile_set(calibration_cfg)
    if expected_service_profile_set is None:
        assert selected_service_profile_set is None
    else:
        assert calibration_cfg["service_profile_set"] == expected_service_profile_set
        assert selected_service_profile_set is not None


def test_calibration_profile_schema_is_explicit_and_minimal() -> None:
    fit_profiles, _ = _load_modules()

    network_profiles = fit_profiles.load_network_profiles(
        _repo_root() / "calibration" / "network_profiles.yaml"
    )
    service_profiles = fit_profiles.load_service_time_profiles(
        _repo_root() / "calibration" / "service_time_profiles.yaml"
    )

    validated_network = fit_profiles.validate_network_profiles(network_profiles)
    validated_service = fit_profiles.validate_service_time_profiles(service_profiles)

    stable_link = validated_network["profiles"]["iot_edge_stable"]
    assert stable_link["source_tier"] == "iot"
    assert stable_link["target_tier"] == "edge"
    assert stable_link["latency_ms"]["median"] > 0
    assert stable_link["bandwidth_mbps"]["sustained"] > 0
    assert stable_link["source"] == "placeholder"

    stage_profile = validated_service["profiles"]["split_inference"]["encoder"]["device"]
    assert stage_profile["distribution"] == "deterministic"
    assert stage_profile["mean_ms"] > 0
    assert stage_profile["p95_ms"] >= stage_profile["mean_ms"]
    assert stage_profile["source"] == "placeholder"


def test_validation_scaffold_accepts_profiles_without_measurements() -> None:
    fit_profiles, validate_rankings = _load_modules()

    network_profiles = fit_profiles.load_network_profiles()
    service_profiles = fit_profiles.load_service_time_profiles()

    report = validate_rankings.build_validation_scaffold(
        network_profiles=network_profiles,
        service_time_profiles=service_profiles,
    )

    assert report["status"] == "pending_measurements"
    assert report["calibration_ready"] is True
    assert report["missing_inputs"] == ["sim_rankings", "real_rankings"]
    assert report["network_profile_count"] >= 2
    assert report["service_profile_count"] >= 3
    assert report["required_ranking_fields"] == ["controller", "rank"]


def test_validation_scaffold_rejects_mismatched_controller_sets() -> None:
    fit_profiles, validate_rankings = _load_modules()

    report = validate_rankings.build_validation_scaffold(
        network_profiles=fit_profiles.load_network_profiles(),
        service_time_profiles=fit_profiles.load_service_time_profiles(),
        sim_rankings=[
            {"controller": "joint", "rank": 1},
            {"controller": "baseline", "rank": 2},
        ],
        real_rankings=[
            {"controller": "joint", "rank": 1},
            {"controller": "oracle", "rank": 2},
        ],
    )

    assert report["status"] == "controller_set_mismatch"
    assert report["calibration_ready"] is False
    assert report["controller_overlap"] == ["joint"]
    assert report["sim_only_controllers"] == ["baseline"]
    assert report["real_only_controllers"] == ["oracle"]


def test_duplicate_network_measurement_names_are_rejected() -> None:
    fit_profiles, _ = _load_modules()

    with pytest.raises(ValueError, match="Duplicate network measurement name"):
        fit_profiles.build_network_profiles_from_measurements(
            [
                {
                    "name": "iot_edge_stable",
                    "source_tier": "iot",
                    "target_tier": "edge",
                    "regime": "stable_low_latency",
                    "median_rtt_ms": 12.0,
                    "p95_rtt_ms": 18.0,
                    "sustained_mbps": 85.0,
                    "degraded_mbps": 55.0,
                    "source": "measured",
                },
                {
                    "name": "iot_edge_stable",
                    "source_tier": "iot",
                    "target_tier": "edge",
                    "regime": "stable_low_latency",
                    "median_rtt_ms": 11.0,
                    "p95_rtt_ms": 17.0,
                    "sustained_mbps": 80.0,
                    "degraded_mbps": 50.0,
                    "source": "measured",
                },
            ]
        )


def test_duplicate_service_measurement_keys_are_rejected() -> None:
    fit_profiles, _ = _load_modules()

    with pytest.raises(ValueError, match="Duplicate service measurement key"):
        fit_profiles.build_service_time_profiles_from_measurements(
            [
                {
                    "workload": "split_inference",
                    "stage": "encoder",
                    "node_class": "device",
                    "distribution": "deterministic",
                    "mean_ms": 18.0,
                    "p95_ms": 24.0,
                    "source": "measured",
                },
                {
                    "workload": "split_inference",
                    "stage": "encoder",
                    "node_class": "device",
                    "distribution": "deterministic",
                    "mean_ms": 19.0,
                    "p95_ms": 25.0,
                    "source": "measured",
                },
            ]
        )


def test_invalid_network_measurement_metadata_is_rejected() -> None:
    fit_profiles, _ = _load_modules()

    with pytest.raises(ValueError, match="non-empty 'source' string"):
        fit_profiles.build_network_profiles_from_measurements(
            [
                {
                    "name": "iot_edge_stable",
                    "source_tier": "iot",
                    "target_tier": "edge",
                    "regime": "stable_low_latency",
                    "median_rtt_ms": 12.0,
                    "p95_rtt_ms": 18.0,
                    "sustained_mbps": 85.0,
                    "degraded_mbps": 55.0,
                    "source": None,
                }
            ]
        )


def test_invalid_service_measurement_metadata_is_rejected() -> None:
    fit_profiles, _ = _load_modules()

    with pytest.raises(ValueError, match="non-empty 'distribution' string"):
        fit_profiles.build_service_time_profiles_from_measurements(
            [
                {
                    "workload": "split_inference",
                    "stage": "encoder",
                    "node_class": "device",
                    "distribution": None,
                    "mean_ms": 18.0,
                    "p95_ms": 24.0,
                    "source": "measured",
                }
            ]
        )


def test_network_measurement_example_uses_records_schema_and_builds_valid_profiles() -> None:
    fit_profiles, _ = _load_modules()

    example_path = _repo_root() / "calibration" / "examples" / "network_measurements_example.yaml"
    payload = _load_yaml_file(example_path)

    assert isinstance(payload, dict)
    assert list(payload) == ["records"]
    assert isinstance(payload["records"], list)
    assert payload["records"]

    profiles = fit_profiles.build_network_profiles_from_measurements(payload["records"])

    assert profiles["schema_version"] == 1
    assert profiles["kind"] == "network_profiles"
    assert len(payload["records"]) == 1
    assert list(profiles["profiles"]) == ["iot_edge_measured_lab"]
    assert "iot_edge_measured_lab" in profiles["profiles"]
    assert profiles["profiles"]["iot_edge_measured_lab"]["source"] != "placeholder"


def test_service_measurement_example_uses_records_schema_and_builds_valid_profiles() -> None:
    fit_profiles, _ = _load_modules()

    example_path = _repo_root() / "calibration" / "examples" / "service_time_measurements_example.yaml"
    payload = _load_yaml_file(example_path)

    assert isinstance(payload, dict)
    assert list(payload) == ["records"]
    assert isinstance(payload["records"], list)
    assert payload["records"]

    profiles = fit_profiles.build_service_time_profiles_from_measurements(payload["records"])

    assert profiles["schema_version"] == 1
    assert profiles["kind"] == "service_time_profiles"
    assert "split_inference_measured_lab" in profiles["profiles"]
    encoder_edge = profiles["profiles"]["split_inference_measured_lab"]["encoder"]["edge"]
    assert encoder_edge["source"] != "placeholder"
    assert encoder_edge["p95_ms"] >= encoder_edge["mean_ms"]


def test_calibration_readme_documents_example_conversion_commands() -> None:
    readme = (_repo_root() / "calibration" / "README.md").read_text(encoding="utf-8")

    assert "collect measurements or emulation records" in readme
    assert "convert them with `fit_profiles.py`" in readme
    assert "rerun a calibrated or trace-backed paper protocol" in readme
    assert "python calibration/fit_profiles.py network --measurements calibration/examples/network_measurements_example.yaml" in readme
    assert "python calibration/fit_profiles.py service --measurements calibration/examples/service_time_measurements_example.yaml" in readme


def test_calibration_readme_documents_builder_compatible_profile_handoff() -> None:
    readme = (_repo_root() / "calibration" / "README.md").read_text(encoding="utf-8")

    assert "calibration.network_profile" in readme
    assert "calibration.service_profile_set" in readme
    assert "src/continuum_bench/calibration/network_profiles.yaml" in readme
    assert "src/continuum_bench/calibration/service_time_profiles.yaml" in readme
    assert "copy the converted `iot_edge_measured_lab` entry into `src/continuum_bench/calibration/network_profiles.yaml`" in readme
    assert "copy the converted `split_inference_measured_lab` workload into `src/continuum_bench/calibration/service_time_profiles.yaml`" in readme
    assert "network_profile: iot_edge_measured_lab" in readme
    assert "service_profile_set: split_inference_measured_lab" in readme


@pytest.mark.parametrize(
    ("config_name", "expected_network_profile", "expected_service_profile_set"),
    [
        ("eo_trace_small.yaml", "space_ground_tracebacked_v1", None),
        ("driving_trace_small.yaml", "vehicular_edge_cloud_tracebacked_v1", None),
        (
            "split_inference_trace_small.yaml",
            "edge_cloud_tracebacked_v1",
            "split_inference_batch_lab_v1",
        ),
        (
            "industrial_iot_trace_small.yaml",
            "edge_cloud_tracebacked_v1",
            "industrial_iot_batch_lab_v1",
        ),
    ],
)
def test_trace_backed_configs_reference_valid_calibration_profiles(
    config_name: str,
    expected_network_profile: str,
    expected_service_profile_set: str | None,
) -> None:
    calibration_profiles = importlib.import_module("continuum_bench.calibration_profiles")
    config = _load_yaml_file(_repo_root() / "configs" / config_name)

    assert config["calibration"]["network_profile"] == expected_network_profile
    selected_network = calibration_profiles.load_selected_network_profile(config["calibration"])
    assert selected_network is not None

    if expected_service_profile_set is None:
        assert config["calibration"].get("service_profile_set") is None
    else:
        assert config["calibration"]["service_profile_set"] == expected_service_profile_set
        selected_service = calibration_profiles.load_service_profile_set(config["calibration"])
        assert selected_service is not None
