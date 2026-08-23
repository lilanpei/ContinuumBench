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
    """Build the EO dual-stream scenario."""

    scenario_cfg = config.get("scenario_config", {})
    app_id = str(scenario_cfg.get("application_id", "EO_DualStream"))

    defaults = _default_profiles()
    stage_cfg = config.get("stages", {})

    stage_profiles: Dict[str, Dict[str, Any]] = {
        name: _merge_dict(defaults[name], stage_cfg.get(name, {})) for name in defaults
    }

    infra_bundle = build_extended_continuum(_with_infra_calibration(config), seed=seed)
    _require_infrastructure_layers(
        "eo_dual_stream",
        infra_bundle.infrastructure,
        _required_layers_from_stage_profiles(stage_profiles),
    )

    pool_cfg = config.get("scaling", {}).get("pools", {}).get("BulkPath", {})
    max_workers = int(pool_cfg.get("max_workers", 4))
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

    space_source = add_stage("SpaceSource", SkeletonRole.SOURCE, stage_profiles["SpaceSource"])
    iot_source = add_stage("IoTSource", SkeletonRole.SOURCE, stage_profiles["IoTSource"])
    fusion = add_stage("Fusion", SkeletonRole.TRANSFORM, stage_profiles["Fusion"])
    triage = add_stage("Triage", SkeletonRole.TRANSFORM, stage_profiles["Triage"])
    fast_path = add_stage("FastPath", SkeletonRole.TRANSFORM, stage_profiles["FastPath"])
    sink = add_stage("Sink", SkeletonRole.SINK, stage_profiles["Sink"])

    bulk_workers: List[str] = []
    for idx in range(max_workers):
        worker_profile = _merge_dict(
            stage_profiles["BulkPathWorker"],
            {"worker_group": "BulkPath", "worker_index": idx},
        )
        worker_id = add_stage(
            f"BulkPath_worker_{idx}",
            SkeletonRole.TRANSFORM,
            worker_profile,
        )
        bulk_workers.append(worker_id)

    app.add_edge(space_source, fusion, strict=False, data_class="eo_space")
    app.add_edge(iot_source, fusion, strict=False, data_class="eo_iot")
    app.add_edge(fusion, triage, strict=False, data_class="fusion")
    app.add_edge(triage, fast_path, strict=False, data_class="fast")
    app.add_edge(fast_path, sink, strict=False, data_class="result")

    for worker_id in bulk_workers:
        app.add_edge(triage, worker_id, strict=False, data_class="bulk")
        app.add_edge(worker_id, sink, strict=False, data_class="result")

    route_specs = {
        space_source: RouteSpec(mode="single", target=fusion),
        iot_source: RouteSpec(mode="single", target=fusion),
        fusion: RouteSpec(mode="single", target=triage),
        triage: RouteSpec(mode="triage", high_target=fast_path, low_pool="BulkPath"),
        fast_path: RouteSpec(mode="single", target=sink),
    }
    for worker_id in bulk_workers:
        route_specs[worker_id] = RouteSpec(mode="single", target=sink)

    return ScenarioBuild(
        name="eo_dual_stream",
        application=app,
        infrastructure=infra_bundle.infrastructure,
        contact_edges=infra_bundle.contact_edges,
        stage_profiles=built_stage_profiles,
        source_stages=[space_source, iot_source],
        sink_stage=sink,
        route_specs=route_specs,
        worker_pools={"BulkPath": bulk_workers},
        source_stream_map={space_source: "SpaceSource", iot_source: "IoTSource"},
    )


def _default_profiles() -> Dict[str, Dict[str, Any]]:
    """Return default stage profiles for the scenario."""

    return {
        "SpaceSource": {
            "priority_prob_high": 0.25,
            "output_size_bytes": 800_000,
            "processing_time_s": 0.0,
            "resources": {
                "cpu": 1.0,
                "ram": 1.0,
                "storage": 32.0,
                "placement_layers": ["space"],
            },
            "buffer": {
                "max_size_bytes": 256 * 1024 * 1024,
                "max_items": 20_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 240.0,
        },
        "IoTSource": {
            "priority_prob_high": 0.08,
            "output_size_bytes": 80_000,
            "processing_time_s": 0.0,
            "resources": {
                "cpu": 0.5,
                "ram": 0.5,
                "storage": 8.0,
                "placement_layers": ["iot"],
            },
            "buffer": {
                "max_size_bytes": 64 * 1024 * 1024,
                "max_items": 8_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 120.0,
        },
        "Fusion": {
            "processing_time_s": 0.7,
            "alpha": 1.0,
            "resources": {
                "cpu": 4.0,
                "ram": 8.0,
                "storage": 64.0,
            },
            "buffer": {
                "max_size_bytes": 512 * 1024 * 1024,
                "max_items": 40_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 180.0,
        },
        "Triage": {
            "processing_time_s": 0.3,
            "alpha": 1.0,
            "resources": {
                "cpu": 3.0,
                "ram": 4.0,
                "storage": 32.0,
            },
            "triage_high_priorities": ["critical", "high"],
            "buffer": {
                "max_size_bytes": 512 * 1024 * 1024,
                "max_items": 40_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 240.0,
        },
        "FastPath": {
            "processing_time_s": 0.5,
            "alpha": 0.35,
            "resources": {
                "cpu": 4.0,
                "ram": 8.0,
                "storage": 64.0,
            },
            "buffer": {
                "max_size_bytes": 256 * 1024 * 1024,
                "max_items": 20_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 180.0,
        },
        "BulkPathWorker": {
            "processing_time_s": 2.5,
            "alpha": 0.9,
            "resources": {
                "cpu": 5.0,
                "ram": 8.0,
                "storage": 128.0,
            },
            "buffer": {
                "max_size_bytes": 768 * 1024 * 1024,
                "max_items": 60_000,
                "on_overflow": "drop_oldest_low",
            },
            "buffer_ttl": 300.0,
        },
        "Sink": {
            "processing_time_s": 0.1,
            "deadline_s": 40.0,
            "resources": {
                "cpu": 1.0,
                "ram": 2.0,
                "storage": 32.0,
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
