from __future__ import annotations

from typing import Any, Dict, List

from continuum_bench.calibration_profiles import require_calibration_config
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
    """Build the driving V2X scenario."""

    scenario_cfg = config.get("scenario_config", {})
    app_id = str(scenario_cfg.get("application_id", "Driving_V2X"))

    defaults = _default_profiles()
    stage_cfg = config.get("stages", {})

    stage_profiles: Dict[str, Dict[str, Any]] = {
        name: _merge_dict(defaults[name], stage_cfg.get(name, {})) for name in defaults
    }

    infra_bundle = build_extended_continuum(_with_infra_calibration(config), seed=seed)
    _require_infrastructure_layers(
        "driving_v2x",
        infra_bundle.infrastructure,
        _required_layers_from_stage_profiles(stage_profiles),
    )

    pool_cfg = config.get("scaling", {}).get("pools", {}).get("Offload", {})
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

    source = add_stage("VehicleSource", SkeletonRole.SOURCE, stage_profiles["VehicleSource"])
    perception = add_stage("Perception", SkeletonRole.TRANSFORM, stage_profiles["Perception"])
    tracker = add_stage("Tracker", SkeletonRole.TRANSFORM, stage_profiles["Tracker"])
    planner = add_stage("Planner", SkeletonRole.TRANSFORM, stage_profiles["Planner"])
    sink = add_stage("Sink", SkeletonRole.SINK, stage_profiles["Sink"])

    offload_workers: List[str] = []
    for idx in range(max_workers):
        worker_profile = _merge_dict(
            stage_profiles["OffloadWorker"],
            {"worker_group": "Offload", "worker_index": idx},
        )
        worker_id = add_stage(
            f"Offload_worker_{idx}",
            SkeletonRole.TRANSFORM,
            worker_profile,
        )
        offload_workers.append(worker_id)

    app.add_edge(source, perception, strict=False, data_class="sensor")
    app.add_edge(perception, tracker, strict=False, data_class="perception")
    app.add_edge(tracker, planner, strict=False, data_class="critical")
    app.add_edge(planner, sink, strict=False, data_class="control")

    for worker_id in offload_workers:
        app.add_edge(tracker, worker_id, strict=False, data_class="offload")
        app.add_edge(worker_id, planner, strict=False, data_class="offload_return")

    route_specs = {
        source: RouteSpec(mode="single", target=perception),
        perception: RouteSpec(mode="single", target=tracker),
        tracker: RouteSpec(mode="triage", high_target=planner, low_pool="Offload"),
        planner: RouteSpec(mode="single", target=sink),
    }
    for worker_id in offload_workers:
        route_specs[worker_id] = RouteSpec(mode="single", target=planner)

    return ScenarioBuild(
        name="driving_v2x",
        application=app,
        infrastructure=infra_bundle.infrastructure,
        contact_edges=infra_bundle.contact_edges,
        stage_profiles=built_stage_profiles,
        source_stages=[source],
        sink_stage=sink,
        route_specs=route_specs,
        worker_pools={"Offload": offload_workers},
        source_stream_map={source: "VehicleSource"},
    )


def _default_profiles() -> Dict[str, Dict[str, Any]]:
    """Return default stage profiles for the scenario."""

    return {
        "VehicleSource": {
            "priority_prob_high": 0.35,
            "output_size_bytes": 320_000,
            "processing_time_s": 0.0,
            "resources": {
                "cpu": 2.0,
                "ram": 4.0,
                "storage": 32.0,
                "placement_layers": ["iot"],
            },
            "buffer": {
                "max_size_bytes": 128 * 1024 * 1024,
                "max_items": 10_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 90.0,
        },
        "Perception": {
            "processing_time_s": 0.8,
            "alpha": 0.65,
            "resources": {"cpu": 6.0, "ram": 12.0, "storage": 128.0},
            "buffer": {
                "max_size_bytes": 256 * 1024 * 1024,
                "max_items": 16_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 90.0,
        },
        "Tracker": {
            "processing_time_s": 0.4,
            "alpha": 1.0,
            "resources": {"cpu": 4.0, "ram": 8.0, "storage": 64.0},
            "triage_high_priorities": ["critical", "high"],
            "buffer": {
                "max_size_bytes": 256 * 1024 * 1024,
                "max_items": 16_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 90.0,
        },
        "OffloadWorker": {
            "processing_time_s": 1.6,
            "alpha": 0.9,
            "resources": {"cpu": 5.0, "ram": 10.0, "storage": 96.0},
            "buffer": {
                "max_size_bytes": 384 * 1024 * 1024,
                "max_items": 20_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 120.0,
        },
        "Planner": {
            "processing_time_s": 0.35,
            "alpha": 1.0,
            "resources": {"cpu": 4.0, "ram": 6.0, "storage": 64.0},
            "buffer": {
                "max_size_bytes": 128 * 1024 * 1024,
                "max_items": 8_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 90.0,
        },
        "Sink": {
            "processing_time_s": 0.05,
            "deadline_s": 10.0,
            "resources": {"cpu": 1.0, "ram": 2.0, "storage": 32.0},
            "buffer": {
                "max_size_bytes": 16 * 1024 * 1024,
                "max_items": 2_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 120.0,
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
