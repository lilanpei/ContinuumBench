from __future__ import annotations

from typing import Any, Dict, List

from continuum_bench.calibration_profiles import (
    load_service_profile_set,
    require_calibration_config,
)
from continuum_bench.infra import build_extended_continuum
from continuum_bench.runtime import SkeletonRole, SkeletonService
from continuum_bench.scenarios import (
    RouteSpec,
    ScenarioBuild,
    _build_application,
    _profile_assets,
    _required_layers_from_stage_profiles,
    _require_infrastructure_layers,
    _with_runtime_aliases,
)


def build(config: Dict[str, Any], seed: int) -> ScenarioBuild:
    """Build the industrial IoT scenario."""

    scenario_cfg = config.get("scenario_config", {})
    app_id = str(scenario_cfg.get("application_id", "IndustrialIoT"))

    defaults = _default_profiles()
    stage_cfg = config.get("stages", {})
    calibrated_stage_defaults = _load_service_profile_defaults(config)

    stage_profiles: Dict[str, Dict[str, Any]] = {
        name: _merge_dict(
            _merge_dict(defaults[name], calibrated_stage_defaults.get(name, {})),
            stage_cfg.get(name, {}),
        )
        for name in defaults
    }

    infra_bundle = build_extended_continuum(_with_infra_calibration(config), seed=seed)
    _require_infrastructure_layers(
        "industrial_iot",
        infra_bundle.infrastructure,
        _required_layers_from_stage_profiles(stage_profiles),
    )

    pool_cfg = config.get("scaling", {}).get("pools", {}).get("BulkArchive", {})
    max_workers = int(pool_cfg.get("max_workers", 3))
    built_stage_profiles: Dict[str, Dict[str, Any]] = {}

    app = _build_application(application_id=app_id)

    def add_stage(name: str, role: SkeletonRole, profile: Dict[str, Any]) -> str:
        """Create one scenario stage and record its profile."""

        resolved_profile = _with_runtime_aliases(profile)
        service_id = f"{app_id}_{name}"
        service = SkeletonService(service_id=service_id, role=role, profile=resolved_profile)
        assets = _profile_assets(name, resolved_profile)
        app.add_service(service, strict=False, **assets)
        built_stage_profiles[service_id] = _with_runtime_aliases(resolved_profile)
        return service_id

    source = add_stage("SensorSource", SkeletonRole.SOURCE, stage_profiles["SensorSource"])
    filter_stage = add_stage("Filter", SkeletonRole.TRANSFORM, stage_profiles["Filter"])
    aggregate = add_stage("Aggregate", SkeletonRole.TRANSFORM, stage_profiles["Aggregate"])
    anomaly = add_stage("AnomalyDetect", SkeletonRole.TRANSFORM, stage_profiles["AnomalyDetect"])
    control = add_stage("ControlDispatch", SkeletonRole.TRANSFORM, stage_profiles["ControlDispatch"])
    sink = add_stage("Sink", SkeletonRole.SINK, stage_profiles["Sink"])
    archive = add_stage("Archive", SkeletonRole.TRANSFORM, stage_profiles["Archive"])

    bulk_archive_workers: List[str] = []
    for idx in range(max_workers):
        worker_profile = _merge_dict(
            stage_profiles["BulkArchiveWorker"],
            {"worker_group": "BulkArchive", "worker_index": idx},
        )
        worker_id = add_stage(
            f"BulkArchive_worker_{idx}",
            SkeletonRole.TRANSFORM,
            worker_profile,
        )
        bulk_archive_workers.append(worker_id)

    app.add_edge(source, filter_stage, strict=False, data_class="sensor")
    app.add_edge(filter_stage, aggregate, strict=False, data_class="filtered")
    app.add_edge(aggregate, anomaly, strict=False, data_class="aggregated")
    app.add_edge(anomaly, control, strict=False, data_class="control")
    app.add_edge(control, sink, strict=False, data_class="actuation")
    app.add_edge(archive, sink, strict=False, data_class="archived_result")

    for worker_id in bulk_archive_workers:
        app.add_edge(anomaly, worker_id, strict=False, data_class="archive")
        app.add_edge(worker_id, archive, strict=False, data_class="archived")

    route_specs = {
        source: RouteSpec(mode="single", target=filter_stage),
        filter_stage: RouteSpec(mode="single", target=aggregate),
        aggregate: RouteSpec(mode="single", target=anomaly),
        anomaly: RouteSpec(mode="triage", high_target=control, low_pool="BulkArchive"),
        control: RouteSpec(mode="single", target=sink),
        archive: RouteSpec(mode="single", target=sink),
    }
    for worker_id in bulk_archive_workers:
        route_specs[worker_id] = RouteSpec(mode="single", target=archive)

    return ScenarioBuild(
        name="industrial_iot",
        application=app,
        infrastructure=infra_bundle.infrastructure,
        contact_edges=infra_bundle.contact_edges,
        stage_profiles=built_stage_profiles,
        source_stages=[source],
        sink_stage=sink,
        route_specs=route_specs,
        worker_pools={"BulkArchive": bulk_archive_workers},
        source_stream_map={source: "SensorSource"},
    )


def _default_profiles() -> Dict[str, Dict[str, Any]]:
    """Return default stage profiles for the scenario."""

    return {
        "SensorSource": {
            "priority_prob_high": 0.12,
            "output_size_bytes": 96_000,
            "processing_time_s": 0.0,
            "resources": {
                "cpu": 1.0,
                "ram": 2.0,
                "storage": 16.0,
                "placement_layers": ["iot"],
            },
            "buffer": {
                "max_size_bytes": 64 * 1024 * 1024,
                "max_items": 8_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 120.0,
        },
        "Filter": {
            "processing_time_s": 0.2,
            "alpha": 0.7,
            "resources": {
                "cpu": 2.0,
                "ram": 4.0,
                "storage": 32.0,
            },
            "buffer": {
                "max_size_bytes": 128 * 1024 * 1024,
                "max_items": 12_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 120.0,
        },
        "Aggregate": {
            "processing_time_s": 0.35,
            "alpha": 0.8,
            "resources": {
                "cpu": 3.0,
                "ram": 6.0,
                "storage": 48.0,
            },
            "buffer": {
                "max_size_bytes": 128 * 1024 * 1024,
                "max_items": 12_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 120.0,
        },
        "AnomalyDetect": {
            "processing_time_s": 0.45,
            "alpha": 1.0,
            "triage_high_priorities": ["critical", "high"],
            "resources": {
                "cpu": 4.0,
                "ram": 8.0,
                "storage": 64.0,
            },
            "buffer": {
                "max_size_bytes": 192 * 1024 * 1024,
                "max_items": 16_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 150.0,
        },
        "ControlDispatch": {
            "processing_time_s": 0.15,
            "alpha": 0.5,
            "resources": {
                "cpu": 2.0,
                "ram": 4.0,
                "storage": 32.0,
            },
            "buffer": {
                "max_size_bytes": 64 * 1024 * 1024,
                "max_items": 8_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 90.0,
        },
        "BulkArchiveWorker": {
            "processing_time_s": 1.5,
            "alpha": 0.9,
            "resources": {
                "cpu": 4.0,
                "ram": 8.0,
                "storage": 96.0,
            },
            "buffer": {
                "max_size_bytes": 256 * 1024 * 1024,
                "max_items": 20_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 240.0,
        },
        "Sink": {
            "processing_time_s": 0.05,
            "deadline_s": 8.0,
            "resources": {
                "cpu": 1.0,
                "ram": 2.0,
                "storage": 16.0,
            },
            "buffer": {
                "max_size_bytes": 16 * 1024 * 1024,
                "max_items": 2_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 120.0,
        },
        "Archive": {
            "processing_time_s": 0.1,
            "resources": {
                "cpu": 1.0,
                "ram": 2.0,
                "storage": 256.0,
            },
            "buffer": {
                "max_size_bytes": 32 * 1024 * 1024,
                "max_items": 4_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 300.0,
        },
    }


def _merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge one level of nested stage configuration."""

    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def _with_infra_calibration(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the infrastructure config with calibration selectors attached."""

    infrastructure_cfg = dict(config.get("infrastructure", {}))
    calibration_cfg = require_calibration_config(
        config.get("calibration"),
        context="scenario",
    )
    if calibration_cfg:
        infrastructure_cfg["calibration"] = calibration_cfg
    return infrastructure_cfg


def _load_service_profile_defaults(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Load calibrated processing-time defaults for stages."""

    workload_profiles = load_service_profile_set(config.get("calibration"))
    if workload_profiles is None:
        return {}

    mapping = {
        "Filter": [("Filter", "edge"), ("rules_engine", "edge")],
        "Aggregate": [("Aggregate", "edge"), ("rules_engine", "edge")],
        "AnomalyDetect": [("AnomalyDetect", "edge"), ("rules_engine", "edge")],
        "ControlDispatch": [("ControlDispatch", "edge"), ("AnomalyDetect", "edge"), ("rules_engine", "edge")],
        "BulkArchiveWorker": [("BulkArchive", "cloud"), ("rules_engine", "cloud")],
        "Archive": [("BulkArchive", "cloud"), ("rules_engine", "cloud")],
    }

    defaults: Dict[str, Dict[str, Any]] = {}
    for stage_name, candidates in mapping.items():
        profile = _service_profile_entry(workload_profiles, candidates)
        defaults[stage_name] = {"processing_time_s": float(profile["mean_ms"]) / 1000.0}
    return defaults


def _service_profile_entry(
    workload_profiles: Dict[str, Any],
    candidates: list[tuple[str, str]],
) -> Dict[str, Any]:
    """Return the first matching calibrated service profile."""

    missing_stage_names: list[str] = []
    missing_node_classes: list[str] = []

    for stage_name, node_class in candidates:
        stage_profiles = workload_profiles.get(stage_name)
        if not isinstance(stage_profiles, dict):
            missing_stage_names.append(stage_name)
            continue

        profile = stage_profiles.get(node_class)
        if not isinstance(profile, dict):
            missing_node_classes.append(f"{stage_name}.{node_class}")
            continue
        return profile

    if missing_node_classes:
        raise ValueError(
            "Missing service-time calibration node class: "
            + ", ".join(missing_node_classes)
        )

    raise ValueError(
        "Missing service-time calibration stage: " + ", ".join(missing_stage_names)
    )
