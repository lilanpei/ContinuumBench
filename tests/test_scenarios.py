from __future__ import annotations

from pathlib import Path

import pytest

from continuum_bench.runner import load_config
from continuum_bench.scenarios import build_scenario


def _service_id_for_stage(scenario, stage_name: str) -> str:
    for service_id, attrs in scenario.application.nodes.items():
        if attrs.get("stage_name") == stage_name:
            return service_id
    raise AssertionError(f"Missing stage {stage_name!r}")


def _infra_link_profile(infrastructure, src: str, dst: str) -> dict[str, float]:
    return dict(getattr(infrastructure, "edges")[(src, dst)])


def _infra_node_for_layer(infrastructure, layer: str) -> str:
    for node, attrs in infrastructure.nodes.items():
        placement_layers = attrs.get("placement_layers", [])
        if isinstance(placement_layers, list) and [str(item).lower() for item in placement_layers] == [layer]:
            return node
    raise AssertionError(f"Missing node in placement layer {layer}")


TRACEBACKED_CONFIG_EXPECTATIONS = [
    ("eo_trace_small.yaml", "eo_dual_stream", {"SpaceSource", "IoTSource"}),
    ("driving_trace_small.yaml", "driving_v2x", {"VehicleSource"}),
    ("split_inference_trace_small.yaml", "split_inference", {"CameraSource"}),
    ("industrial_iot_trace_small.yaml", "industrial_iot", {"SensorSource"}),
]


@pytest.mark.parametrize(
    ("scenario_name", "pool_name", "expected_sources", "expected_workers"),
    [
        ("eo_dual_stream", "BulkPath", 2, 4),
        ("driving_v2x", "Offload", 1, 3),
        ("split_inference", "CloudRefine", 1, 2),
        ("industrial_iot", "BulkArchive", 1, 3),
    ],
)
def test_build_scenario_constructs_expected_surface(
    scenario_name: str,
    pool_name: str,
    expected_sources: int,
    expected_workers: int,
) -> None:
    scenario = build_scenario(scenario_name, config={}, seed=7)

    assert scenario.name == scenario_name
    assert len(scenario.source_stages) == expected_sources
    assert len(scenario.worker_pools[pool_name]) == expected_workers
    assert set(scenario.worker_pools[pool_name]).issubset(set(scenario.application.nodes))
    assert scenario.sink_stage in scenario.application.nodes
    assert scenario.contact_edges


def test_anchor_layers_are_exposed_as_eclypse_symbolic_assets() -> None:
    scenario = build_scenario("split_inference", config={}, seed=7)
    source_id = _service_id_for_stage(scenario, "CameraSource")

    assert scenario.application.nodes[source_id]["placement_layers"] == ["iot"]
    assert scenario.infrastructure.nodes["IoT_0"]["placement_layers"] == ["iot"]
    assert scenario.infrastructure.nodes["Cloud_0"]["placement_layers"] == ["cloud"]


def test_split_inference_exposes_expected_routes_and_source_anchor() -> None:
    scenario = build_scenario("split_inference", config={}, seed=7)

    source_id = _service_id_for_stage(scenario, "CameraSource")
    preprocess_id = _service_id_for_stage(scenario, "EdgePreprocess")
    inference_id = _service_id_for_stage(scenario, "EdgeInference")
    sink_id = _service_id_for_stage(scenario, "Sink")
    worker_id = scenario.worker_pools["CloudRefine"][0]

    assert scenario.route_specs[source_id].target == preprocess_id
    assert scenario.route_specs[preprocess_id].target == inference_id

    inference_route = scenario.route_specs[inference_id]
    assert inference_route.mode == "triage"
    assert inference_route.high_target == sink_id
    assert inference_route.low_pool == "CloudRefine"

    assert scenario.route_specs[worker_id].target == sink_id

    assert scenario.application.nodes[source_id]["placement_layers"] == ["iot"]
    assert scenario.application.nodes[preprocess_id]["placement_layers"] == []
    assert scenario.application.nodes[inference_id]["placement_layers"] == []
    assert scenario.application.nodes[sink_id]["placement_layers"] == []
    assert scenario.application.nodes[worker_id]["placement_layers"] == []


def test_split_inference_fast_path_completes_at_edge_sink() -> None:
    scenario = build_scenario("split_inference", config={}, seed=7)

    inference_id = _service_id_for_stage(scenario, "EdgeInference")
    sink_id = _service_id_for_stage(scenario, "Sink")

    assert scenario.route_specs[inference_id].high_target == sink_id
    assert scenario.application.nodes[sink_id]["placement_layers"] == []


def test_eo_dual_stream_only_sources_are_tier_anchored_by_default() -> None:
    scenario = build_scenario("eo_dual_stream", config={}, seed=7)

    expected_layers = {
        "SpaceSource": ["space"],
        "IoTSource": ["iot"],
        "Fusion": [],
        "Triage": [],
        "FastPath": [],
        "Sink": [],
    }
    for stage_name, expected in expected_layers.items():
        service_id = _service_id_for_stage(scenario, stage_name)
        assert scenario.application.nodes[service_id]["placement_layers"] == expected

    worker_id = scenario.worker_pools["BulkPath"][0]
    assert scenario.application.nodes[worker_id]["placement_layers"] == []


def test_split_inference_sample_config_builds_expected_surface() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "split_inference_small.yaml")

    assert config["scenario"] == "split_inference"
    assert set(config["workload"]["streams"]) == {"CameraSource"}

    scenario = build_scenario(config["scenario"], config=config, seed=7)

    assert scenario.name == "split_inference"
    assert scenario.source_stream_map[scenario.source_stages[0]] == "CameraSource"


@pytest.mark.parametrize(
    ("config_name", "expected_scenario", "expected_streams"),
    TRACEBACKED_CONFIG_EXPECTATIONS,
)
def test_tracebacked_sample_configs_build_expected_surface(
    config_name: str,
    expected_scenario: str,
    expected_streams: set[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / config_name)

    assert config["scenario"] == expected_scenario
    assert set(config["workload"]["streams"]) == expected_streams

    scenario = build_scenario(config["scenario"], config=config, seed=7)

    assert scenario.name == expected_scenario
    assert {scenario.source_stream_map[source_id] for source_id in scenario.source_stages} == expected_streams


def test_split_inference_keeps_default_service_times_without_calibration_config() -> None:
    scenario = build_scenario("split_inference", config={}, seed=7)

    preprocess_id = _service_id_for_stage(scenario, "EdgePreprocess")
    inference_id = _service_id_for_stage(scenario, "EdgeInference")
    worker_id = scenario.worker_pools["CloudRefine"][0]

    assert scenario.stage_profiles[preprocess_id]["processing_time_s"] == pytest.approx(0.45)
    assert scenario.stage_profiles[inference_id]["processing_time_s"] == pytest.approx(0.6)
    assert scenario.stage_profiles[worker_id]["processing_time_s"] == pytest.approx(1.8)


def test_split_inference_small_config_keeps_default_service_times_without_calibration_opt_in() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "split_inference_small.yaml")
    scenario = build_scenario(config["scenario"], config=config, seed=7)

    preprocess_id = _service_id_for_stage(scenario, "EdgePreprocess")
    inference_id = _service_id_for_stage(scenario, "EdgeInference")
    worker_id = scenario.worker_pools["CloudRefine"][0]

    assert scenario.stage_profiles[preprocess_id]["processing_time_s"] == pytest.approx(0.45)
    assert scenario.stage_profiles[inference_id]["processing_time_s"] == pytest.approx(0.6)
    assert scenario.stage_profiles[worker_id]["processing_time_s"] == pytest.approx(1.8)


def test_split_inference_applies_service_time_profile_set_when_requested() -> None:
    scenario = build_scenario(
        "split_inference",
        config={
            "calibration": {"service_profile_set": "split_inference"},
        },
        seed=7,
    )

    preprocess_id = _service_id_for_stage(scenario, "EdgePreprocess")
    inference_id = _service_id_for_stage(scenario, "EdgeInference")
    worker_id = scenario.worker_pools["CloudRefine"][0]

    assert scenario.stage_profiles[preprocess_id]["processing_time_s"] == pytest.approx(0.0075)
    assert scenario.stage_profiles[inference_id]["processing_time_s"] == pytest.approx(0.0075)
    assert scenario.stage_profiles[worker_id]["processing_time_s"] == pytest.approx(0.004)


def test_split_inference_stage_override_wins_over_service_time_calibration() -> None:
    scenario = build_scenario(
        "split_inference",
        config={
            "calibration": {"service_profile_set": "split_inference"},
            "stages": {
                "EdgeInference": {"processing_time_s": 0.321},
                "CloudRefineWorker": {"processing_time_s": 0.654},
            },
        },
        seed=7,
    )

    inference_id = _service_id_for_stage(scenario, "EdgeInference")
    worker_id = scenario.worker_pools["CloudRefine"][0]

    assert scenario.stage_profiles[inference_id]["processing_time_s"] == pytest.approx(0.321)
    assert scenario.stage_profiles[worker_id]["processing_time_s"] == pytest.approx(0.654)


def test_industrial_iot_applies_service_time_profile_set_when_requested() -> None:
    scenario = build_scenario(
        "industrial_iot",
        config={
            "calibration": {"service_profile_set": "industrial_iot"},
        },
        seed=7,
    )

    filter_id = _service_id_for_stage(scenario, "Filter")
    aggregate_id = _service_id_for_stage(scenario, "Aggregate")
    anomaly_id = _service_id_for_stage(scenario, "AnomalyDetect")
    control_id = _service_id_for_stage(scenario, "ControlDispatch")
    worker_id = scenario.worker_pools["BulkArchive"][0]
    archive_id = _service_id_for_stage(scenario, "Archive")

    assert scenario.stage_profiles[filter_id]["processing_time_s"] == pytest.approx(0.006)
    assert scenario.stage_profiles[aggregate_id]["processing_time_s"] == pytest.approx(0.006)
    assert scenario.stage_profiles[anomaly_id]["processing_time_s"] == pytest.approx(0.006)
    assert scenario.stage_profiles[control_id]["processing_time_s"] == pytest.approx(0.006)
    assert scenario.stage_profiles[worker_id]["processing_time_s"] == pytest.approx(0.0035)
    assert scenario.stage_profiles[archive_id]["processing_time_s"] == pytest.approx(0.0035)


@pytest.mark.parametrize(
    ("scenario_name", "config", "error_match"),
    [
        (
            "split_inference",
            {"calibration": "bad"},
            "calibration.*mapping",
        ),
        (
            "split_inference",
            {"calibration": {"service_profile_set": ""}},
            "service_profile_set",
        ),
        (
            "industrial_iot",
            {"calibration": {"service_profile_set": 123}},
            "service_profile_set",
        ),
    ],
)
def test_touched_scenarios_validate_malformed_calibration_config(
    scenario_name: str,
    config: dict[str, object],
    error_match: str,
) -> None:
    with pytest.raises(ValueError, match=error_match):
        build_scenario(scenario_name, config=config, seed=7)


def test_industrial_iot_exposes_control_and_bulk_paths() -> None:
    scenario = build_scenario("industrial_iot", config={}, seed=7)

    source_id = _service_id_for_stage(scenario, "SensorSource")
    filter_id = _service_id_for_stage(scenario, "Filter")
    aggregate_id = _service_id_for_stage(scenario, "Aggregate")
    anomaly_id = _service_id_for_stage(scenario, "AnomalyDetect")
    fast_id = _service_id_for_stage(scenario, "ControlDispatch")
    sink_id = _service_id_for_stage(scenario, "Sink")
    archive_id = _service_id_for_stage(scenario, "Archive")
    worker_id = scenario.worker_pools["BulkArchive"][0]

    assert scenario.route_specs[source_id].target == filter_id
    assert scenario.route_specs[filter_id].target == aggregate_id
    assert scenario.route_specs[aggregate_id].target == anomaly_id

    anomaly_route = scenario.route_specs[anomaly_id]
    assert anomaly_route.mode == "triage"
    assert anomaly_route.high_target == fast_id
    assert anomaly_route.low_pool == "BulkArchive"

    assert scenario.route_specs[fast_id].target == sink_id
    assert scenario.route_specs[worker_id].target == archive_id
    assert scenario.route_specs[archive_id].target == sink_id
    assert scenario.sink_stage == sink_id

    assert scenario.application.nodes[source_id]["placement_layers"] == ["iot"]
    assert scenario.application.nodes[filter_id]["placement_layers"] == []
    assert scenario.application.nodes[aggregate_id]["placement_layers"] == []
    assert scenario.application.nodes[anomaly_id]["placement_layers"] == []
    assert scenario.application.nodes[fast_id]["placement_layers"] == []
    assert scenario.application.nodes[sink_id]["placement_layers"] == []
    assert scenario.application.nodes[worker_id]["placement_layers"] == []
    assert scenario.application.nodes[archive_id]["placement_layers"] == []


def test_industrial_iot_archive_stage_feeds_single_runner_sink() -> None:
    scenario = build_scenario("industrial_iot", config={}, seed=7)

    archive_id = _service_id_for_stage(scenario, "Archive")
    sink_id = _service_id_for_stage(scenario, "Sink")

    assert archive_id != scenario.sink_stage
    assert scenario.route_specs[archive_id].mode == "single"
    assert scenario.route_specs[archive_id].target == sink_id
    assert getattr(scenario.application.services[archive_id].role, "name", "").lower() == "transform"


def test_industrial_iot_archive_stage_avoids_competing_deadline_semantics() -> None:
    scenario = build_scenario("industrial_iot", config={}, seed=7)

    archive_id = _service_id_for_stage(scenario, "Archive")
    sink_id = _service_id_for_stage(scenario, "Sink")

    archive_profile = scenario.stage_profiles[archive_id]
    sink_profile = scenario.stage_profiles[sink_id]

    assert scenario.route_specs[archive_id].target == sink_id
    assert "deadline_s" in sink_profile
    assert "deadline" in sink_profile
    assert "deadline_s" not in archive_profile
    assert "deadline" not in archive_profile
    assert "deadline_s" not in scenario.application.services[archive_id].profile
    assert "deadline" not in scenario.application.services[archive_id].profile


def test_industrial_iot_archive_path_has_graph_edge_to_main_sink() -> None:
    scenario = build_scenario("industrial_iot", config={}, seed=7)

    archive_id = _service_id_for_stage(scenario, "Archive")
    sink_id = _service_id_for_stage(scenario, "Sink")

    assert (archive_id, sink_id) in scenario.application.edges


def test_industrial_iot_sample_config_builds_expected_surface() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "industrial_iot_small.yaml")

    assert config["scenario"] == "industrial_iot"
    assert set(config["workload"]["streams"]) == {"SensorSource"}

    scenario = build_scenario(config["scenario"], config=config, seed=7)

    assert scenario.name == "industrial_iot"
    assert scenario.source_stream_map[scenario.source_stages[0]] == "SensorSource"


def test_industrial_iot_small_config_keeps_default_service_times_without_calibration_opt_in() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "industrial_iot_small.yaml")
    scenario = build_scenario(config["scenario"], config=config, seed=7)

    filter_id = _service_id_for_stage(scenario, "Filter")
    aggregate_id = _service_id_for_stage(scenario, "Aggregate")
    anomaly_id = _service_id_for_stage(scenario, "AnomalyDetect")
    control_id = _service_id_for_stage(scenario, "ControlDispatch")
    worker_id = scenario.worker_pools["BulkArchive"][0]
    archive_id = _service_id_for_stage(scenario, "Archive")

    assert scenario.stage_profiles[filter_id]["processing_time_s"] == pytest.approx(0.2)
    assert scenario.stage_profiles[aggregate_id]["processing_time_s"] == pytest.approx(0.35)
    assert scenario.stage_profiles[anomaly_id]["processing_time_s"] == pytest.approx(0.45)
    assert scenario.stage_profiles[control_id]["processing_time_s"] == pytest.approx(0.15)
    assert scenario.stage_profiles[worker_id]["processing_time_s"] == pytest.approx(1.5)
    assert scenario.stage_profiles[archive_id]["processing_time_s"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("config_name", "src", "dst", "expected_latency", "expected_bandwidth"),
    [
        ("eo_small.yaml", "Edge_0", "Cloud_0", 20.0, 1000.0),
        ("eo_small.yaml", "IoT_0", "Edge_0", 6.0, 120.0),
        ("eo_small.yaml", "Space_0", "Edge_0", 220.0, 0.0),
        ("driving_small.yaml", "Edge_0", "Cloud_0", 20.0, 1000.0),
        ("driving_small.yaml", "IoT_0", "Edge_0", 6.0, 120.0),
        ("split_inference_small.yaml", "Edge_0", "Cloud_0", 20.0, 1000.0),
        ("split_inference_small.yaml", "IoT_0", "Edge_0", 6.0, 120.0),
        ("industrial_iot_small.yaml", "Edge_0", "Cloud_0", 20.0, 1000.0),
        ("industrial_iot_small.yaml", "IoT_0", "Edge_0", 6.0, 120.0),
    ],
)
def test_existing_small_configs_keep_default_network_links_without_calibration_opt_in(
    config_name: str,
    src: str,
    dst: str,
    expected_latency: float,
    expected_bandwidth: float,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / config_name)
    scenario = build_scenario(config["scenario"], config=config, seed=7)

    profile = _infra_link_profile(scenario.infrastructure, src, dst)

    assert profile["latency"] == pytest.approx(expected_latency)
    assert profile["bandwidth"] == pytest.approx(expected_bandwidth)


@pytest.mark.parametrize(
    ("config_name", "scenario_name", "expected_streams"),
    [
        ("eo_trace_small.yaml", "eo_dual_stream", {"SpaceSource", "IoTSource"}),
        ("driving_trace_small.yaml", "driving_v2x", {"VehicleSource"}),
        ("split_inference_trace_small.yaml", "split_inference", {"CameraSource"}),
        ("industrial_iot_trace_small.yaml", "industrial_iot", {"SensorSource"}),
    ],
)
def test_trace_backed_configs_build_expected_scenarios(
    config_name: str,
    scenario_name: str,
    expected_streams: set[str],
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / config_name)

    assert config["scenario"] == scenario_name
    assert set(config["workload"]["streams"]) == expected_streams
    assert "calibration" in config

    scenario = build_scenario(config["scenario"], config=config, seed=7)

    assert scenario.name == scenario_name


def test_eo_trace_config_applies_space_edge_tracebacked_baseline() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "eo_trace_small.yaml")

    scenario = build_scenario(config["scenario"], config=config, seed=7)
    infra = scenario.infrastructure
    space = _infra_node_for_layer(infra, "space")
    edge = _infra_node_for_layer(infra, "edge")

    link = infra.path_resources(space, edge)

    assert link["latency"] == pytest.approx(540.0)
    assert link["bandwidth"] == pytest.approx(28.0)


def test_driving_trace_config_applies_vehicular_edge_cloud_profile() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "driving_trace_small.yaml")

    scenario = build_scenario(config["scenario"], config=config, seed=7)
    infra = scenario.infrastructure
    edge = _infra_node_for_layer(infra, "edge")
    cloud = _infra_node_for_layer(infra, "cloud")

    link = infra.path_resources(edge, cloud)

    assert link["latency"] == pytest.approx(28.0)
    assert link["bandwidth"] == pytest.approx(95.0)


def test_split_inference_trace_config_applies_task_normalized_tracebacked_service_times() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "split_inference_trace_small.yaml")

    scenario = build_scenario(config["scenario"], config=config, seed=7)
    preprocess_id = _service_id_for_stage(scenario, "EdgePreprocess")
    inference_id = _service_id_for_stage(scenario, "EdgeInference")
    worker_id = scenario.worker_pools["CloudRefine"][0]

    assert scenario.stage_profiles[preprocess_id]["processing_time_s"] == pytest.approx(0.16)
    assert scenario.stage_profiles[inference_id]["processing_time_s"] == pytest.approx(0.3)
    assert scenario.stage_profiles[worker_id]["processing_time_s"] == pytest.approx(1.02)


def test_industrial_iot_trace_config_applies_task_normalized_tracebacked_service_times() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "industrial_iot_trace_small.yaml")

    scenario = build_scenario(config["scenario"], config=config, seed=7)
    filter_id = _service_id_for_stage(scenario, "Filter")
    aggregate_id = _service_id_for_stage(scenario, "Aggregate")
    anomaly_id = _service_id_for_stage(scenario, "AnomalyDetect")
    worker_id = scenario.worker_pools["BulkArchive"][0]

    assert scenario.stage_profiles[filter_id]["processing_time_s"] == pytest.approx(0.12)
    assert scenario.stage_profiles[aggregate_id]["processing_time_s"] == pytest.approx(0.16)
    assert scenario.stage_profiles[anomaly_id]["processing_time_s"] == pytest.approx(0.24)
    assert scenario.stage_profiles[worker_id]["processing_time_s"] == pytest.approx(1.0)


def test_build_scenario_preserves_worker_specific_stage_profiles() -> None:
    scenario = build_scenario("eo_dual_stream", config={}, seed=7)

    worker_0, worker_1 = scenario.worker_pools["BulkPath"][:2]

    assert scenario.stage_profiles[worker_0]["worker_group"] == "BulkPath"
    assert scenario.stage_profiles[worker_0]["worker_index"] == 0
    assert scenario.stage_profiles[worker_1]["worker_group"] == "BulkPath"
    assert scenario.stage_profiles[worker_1]["worker_index"] == 1


@pytest.mark.parametrize(
    ("scenario_name", "source_stage", "transform_stage", "sink_stage"),
    [
        ("eo_dual_stream", "SpaceSource", "Fusion", "Sink"),
        ("driving_v2x", "VehicleSource", "Perception", "Sink"),
    ],
)
def test_build_scenario_exposes_runtime_aliases_on_profiles_and_services(
    scenario_name: str,
    source_stage: str,
    transform_stage: str,
    sink_stage: str,
) -> None:
    scenario = build_scenario(scenario_name, config={}, seed=7)

    source_id = _service_id_for_stage(scenario, source_stage)
    transform_id = _service_id_for_stage(scenario, transform_stage)
    sink_id = _service_id_for_stage(scenario, sink_stage)

    source_profile = scenario.stage_profiles[source_id]
    transform_profile = scenario.stage_profiles[transform_id]
    sink_profile = scenario.stage_profiles[sink_id]

    assert source_profile["data_size"] == source_profile["output_size_bytes"]
    assert transform_profile["processing_time"] == transform_profile["processing_time_s"]
    assert sink_profile["deadline"] == sink_profile["deadline_s"]

    assert scenario.application.services[source_id].profile["data_size"] == source_profile["data_size"]
    assert (
        scenario.application.services[transform_id].profile["processing_time"]
        == transform_profile["processing_time"]
    )
    assert scenario.application.services[sink_id].profile["deadline"] == sink_profile["deadline"]

    assert scenario.application.nodes[transform_id]["processing_time"] == transform_profile["processing_time"]


def test_build_scenario_rejects_invalid_topology_counts() -> None:
    with pytest.raises(ValueError, match="n_edge"):
        build_scenario(
            "eo_dual_stream",
            config={"infrastructure": {"n_edge": 0}},
            seed=7,
        )


@pytest.mark.parametrize(
    ("scenario_name", "infrastructure_config", "expected_layer"),
    [
        ("eo_dual_stream", {"n_space": 0}, "space"),
        ("driving_v2x", {"n_iot": 0}, "iot"),
        ("split_inference", {"n_iot": 0}, "iot"),
        ("industrial_iot", {"n_iot": 0}, "iot"),
    ],
)
def test_build_scenario_rejects_missing_anchored_layers(
    scenario_name: str,
    infrastructure_config: dict[str, int],
    expected_layer: str,
) -> None:
    with pytest.raises(ValueError, match=expected_layer):
        build_scenario(
            scenario_name,
            config={"infrastructure": infrastructure_config},
            seed=7,
        )


def test_build_scenario_profiles_do_not_share_nested_buffer_state() -> None:
    scenario = build_scenario("eo_dual_stream", config={}, seed=7)

    worker_0, worker_1 = scenario.worker_pools["BulkPath"][:2]
    worker_1_max_items = scenario.stage_profiles[worker_1]["buffer"]["max_items"]

    scenario.application.services[worker_0].profile["buffer"]["max_items"] = 111

    assert scenario.stage_profiles[worker_0]["buffer"]["max_items"] != 111
    assert scenario.stage_profiles[worker_1]["buffer"]["max_items"] == worker_1_max_items

    scenario.stage_profiles[worker_0]["buffer"]["max_items"] = 222

    assert scenario.application.services[worker_0].profile["buffer"]["max_items"] == 111
    assert scenario.stage_profiles[worker_1]["buffer"]["max_items"] == worker_1_max_items


def test_build_scenario_allows_missing_layer_when_stage_override_removes_anchor() -> None:
    scenario = build_scenario(
        "eo_dual_stream",
        config={
            "infrastructure": {"n_space": 0},
            "stages": {
                "SpaceSource": {
                    "resources": {
                        "placement_layers": [],
                    }
                }
            },
        },
        seed=7,
    )

    assert scenario.name == "eo_dual_stream"


def test_build_scenario_requires_layer_added_by_stage_override() -> None:
    with pytest.raises(ValueError, match="cloud"):
        build_scenario(
            "driving_v2x",
            config={
                "infrastructure": {"n_cloud": 0},
                "stages": {
                    "Perception": {
                        "resources": {
                            "placement_layers": ["cloud"],
                        }
                    }
                },
            },
            seed=7,
        )
