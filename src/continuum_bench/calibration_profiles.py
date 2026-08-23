from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

import yaml


_CALIBRATION_DIR = Path(__file__).resolve().parent / "calibration"
_NETWORK_PROFILES_PATH = _CALIBRATION_DIR / "network_profiles.yaml"
_SERVICE_TIME_PROFILES_PATH = _CALIBRATION_DIR / "service_time_profiles.yaml"
_ENERGY_PROFILES_PATH = _CALIBRATION_DIR / "energy_profiles.yaml"


def require_calibration_config(calibration_cfg: Any, *, context: str) -> Dict[str, Any]:
    """Normalize an optional calibration block into a plain mapping."""

    if calibration_cfg is None:
        return {}
    if not isinstance(calibration_cfg, Mapping):
        raise ValueError(f"{context} calibration config must be a mapping")
    return dict(calibration_cfg)


def load_selected_network_profile(calibration_cfg: Any) -> Dict[str, Any] | None:
    """Load the selected network calibration profile, if configured."""

    config = require_calibration_config(calibration_cfg, context="infrastructure")
    profile_name = config.get("network_profile")
    if profile_name is None:
        return None
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise ValueError("infrastructure calibration.network_profile must be a non-empty string")

    profiles = _validate_network_profiles(_load_yaml_mapping(_NETWORK_PROFILES_PATH))["profiles"]
    selected = profiles.get(profile_name)
    if selected is None:
        raise ValueError(f"Unknown network calibration profile: {profile_name}")
    return dict(selected)


def load_service_profile_set(calibration_cfg: Any) -> Dict[str, Any] | None:
    """Load the selected service-time calibration profile set, if configured."""

    config = require_calibration_config(calibration_cfg, context="scenario")
    profile_set_name = config.get("service_profile_set")
    if profile_set_name is None:
        return None
    if not isinstance(profile_set_name, str) or not profile_set_name.strip():
        raise ValueError("calibration.service_profile_set must be a non-empty string")

    workloads = _validate_service_time_profiles(_load_yaml_mapping(_SERVICE_TIME_PROFILES_PATH))["profiles"]
    selected = workloads.get(profile_set_name)
    if selected is None:
        raise ValueError(f"Unknown service-time calibration profile set: {profile_set_name}")
    return dict(selected)


def load_selected_energy_profile(calibration_cfg: Any) -> Dict[str, Any] | None:
    """Load the selected energy calibration profile, if configured."""

    config = require_calibration_config(calibration_cfg, context="energy")
    profile_name = config.get("energy_profile")
    if profile_name is None:
        return None
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise ValueError("calibration.energy_profile must be a non-empty string")

    profiles = _validate_energy_profiles(_load_yaml_mapping(_ENERGY_PROFILES_PATH))["profiles"]
    selected = profiles.get(profile_name)
    if selected is None:
        raise ValueError(f"Unknown energy calibration profile: {profile_name}")
    return dict(selected)


def _load_yaml_mapping(path: Path) -> Dict[str, Any]:
    """Load a YAML file and require a mapping at the root."""

    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError(f"Profile file {path} must be a YAML mapping")
    return dict(payload)


def _require_string(payload: Mapping[str, Any], key: str, context: str) -> str:
    """Read a required non-empty string field from a mapping."""

    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must define a non-empty '{key}' string")
    return value.strip()


def _require_number(
    payload: Mapping[str, Any],
    key: str,
    context: str,
    *,
    strictly_positive: bool = False,
    non_negative: bool = False,
) -> float:
    """Read a numeric field and enforce optional range constraints."""

    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must define numeric '{key}'")

    number = float(value)
    if strictly_positive and number <= 0.0:
        raise ValueError(f"{context} must define positive '{key}'")
    if non_negative and number < 0.0:
        raise ValueError(f"{context} must define non-negative '{key}'")
    return number


def _as_mapping(value: Any, context: str) -> Dict[str, Any]:
    """Require a YAML value to be a mapping and copy it."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a YAML mapping")
    return dict(value)


def _validate_network_profiles(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate the network calibration profile document shape."""

    root = _as_mapping(payload, "Network profiles")
    if root.get("schema_version") != 1:
        raise ValueError("Network profiles must declare schema_version: 1")
    if root.get("kind") != "network_profiles":
        raise ValueError("Network profiles must declare kind: network_profiles")

    profiles = _as_mapping(root.get("profiles"), "Network profiles 'profiles'")
    if not profiles:
        raise ValueError("Network profiles must contain at least one profile entry")

    for profile_name, raw_profile in profiles.items():
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("Network profile names must be non-empty strings")

        context = f"Network profile '{profile_name}'"
        profile = _as_mapping(raw_profile, context)
        _require_string(profile, "source_tier", context)
        _require_string(profile, "target_tier", context)
        _require_string(profile, "regime", context)
        _require_string(profile, "source", context)

        latency = _as_mapping(profile.get("latency_ms"), f"{context} latency_ms")
        median = _require_number(latency, "median", f"{context} latency_ms", strictly_positive=True)
        p95 = _require_number(latency, "p95", f"{context} latency_ms", strictly_positive=True)
        if p95 < median:
            raise ValueError(f"{context} latency p95 must be >= median")

        bandwidth = _as_mapping(profile.get("bandwidth_mbps"), f"{context} bandwidth_mbps")
        sustained = _require_number(
            bandwidth,
            "sustained",
            f"{context} bandwidth_mbps",
            strictly_positive=True,
        )
        degraded = _require_number(
            bandwidth,
            "degraded",
            f"{context} bandwidth_mbps",
            strictly_positive=True,
        )
        if degraded > sustained:
            raise ValueError(f"{context} degraded bandwidth must be <= sustained bandwidth")

        _require_number(profile, "loss_percent", context, non_negative=True)

    return root


def _validate_service_time_profiles(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate the service-time calibration profile document shape."""

    root = _as_mapping(payload, "Service-time profiles")
    if root.get("schema_version") != 1:
        raise ValueError("Service-time profiles must declare schema_version: 1")
    if root.get("kind") != "service_time_profiles":
        raise ValueError("Service-time profiles must declare kind: service_time_profiles")

    workloads = _as_mapping(root.get("profiles"), "Service-time profiles 'profiles'")
    if not workloads:
        raise ValueError("Service-time profiles must contain at least one workload")

    for workload_name, raw_workload in workloads.items():
        if not isinstance(workload_name, str) or not workload_name.strip():
            raise ValueError("Service-time workload names must be non-empty strings")

        workload = _as_mapping(raw_workload, f"Service-time workload '{workload_name}'")
        if not workload:
            raise ValueError(f"Service-time workload '{workload_name}' must define stages")

        for stage_name, raw_stage in workload.items():
            if not isinstance(stage_name, str) or not stage_name.strip():
                raise ValueError(f"Service-time workload '{workload_name}' has an invalid stage name")

            stage = _as_mapping(raw_stage, f"Stage '{workload_name}.{stage_name}'")
            if not stage:
                raise ValueError(f"Stage '{workload_name}.{stage_name}' must define node classes")

            for node_class, raw_profile in stage.items():
                if not isinstance(node_class, str) or not node_class.strip():
                    raise ValueError(
                        f"Stage '{workload_name}.{stage_name}' has an invalid node class"
                    )

                context = f"Service-time profile '{workload_name}.{stage_name}.{node_class}'"
                profile = _as_mapping(raw_profile, context)
                _require_string(profile, "distribution", context)
                mean_ms = _require_number(profile, "mean_ms", context, strictly_positive=True)
                p95_ms = _require_number(profile, "p95_ms", context, strictly_positive=True)
                if p95_ms < mean_ms:
                    raise ValueError(f"{context} must define p95_ms >= mean_ms")

                if "startup_ms" in profile:
                    _require_number(profile, "startup_ms", context, non_negative=True)
                if "migration_ms" in profile:
                    _require_number(profile, "migration_ms", context, non_negative=True)
                _require_string(profile, "source", context)

    return root


def _validate_energy_profiles(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate the energy calibration profile document shape."""

    root = _as_mapping(payload, "Energy profiles")
    if root.get("schema_version") != 1:
        raise ValueError("Energy profiles must declare schema_version: 1")
    if root.get("kind") != "energy_profiles":
        raise ValueError("Energy profiles must declare kind: energy_profiles")

    profiles = _as_mapping(root.get("profiles"), "Energy profiles 'profiles'")
    if not profiles:
        raise ValueError("Energy profiles must contain at least one profile entry")

    for profile_name, raw_profile in profiles.items():
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("Energy profile names must be non-empty strings")

        context = f"Energy profile '{profile_name}'"
        profile = _as_mapping(raw_profile, context)
        _require_string(profile, "domain", context)
        _require_string(profile, "source", context)

        node_tiers = _as_mapping(profile.get("node_tiers"), f"{context} node_tiers")
        if not node_tiers:
            raise ValueError(f"{context} must define at least one node tier")
        for tier_name, raw_tier in node_tiers.items():
            tier_context = f"{context} node tier '{tier_name}'"
            tier = _as_mapping(raw_tier, tier_context)
            _require_number(tier, "idle_power_w", tier_context, non_negative=True)
            for key in (
                "dynamic_power_w_per_cpu",
                "dynamic_power_w_per_ram",
                "dynamic_power_w_per_storage",
            ):
                if key in tier:
                    _require_number(tier, key, tier_context, non_negative=True)
            if "battery_capacity_j" in tier:
                _require_number(
                    tier,
                    "battery_capacity_j",
                    tier_context,
                    strictly_positive=True,
                )

        link_tiers = _as_mapping(profile.get("link_tiers"), f"{context} link_tiers")
        if not link_tiers:
            raise ValueError(f"{context} must define at least one link tier")
        for tier_name, raw_tier in link_tiers.items():
            tier = _as_mapping(raw_tier, f"{context} link tier '{tier_name}'")
            _require_number(
                tier,
                "energy_j_per_byte",
                f"{context} link tier '{tier_name}'",
                non_negative=True,
            )

    return root


__all__ = [
    "load_selected_network_profile",
    "load_selected_energy_profile",
    "load_service_profile_set",
    "require_calibration_config",
]
