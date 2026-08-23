from __future__ import annotations

from typing import Any, Dict, Mapping


_REGIME_LABELS = {
    "F0": "deterministic_synthetic",
    "F1": "contact_mobility_aware",
    "F2": "calibrated_trace_backed",
}


_TIER_LABELS = {
    "T0": "base_deterministic",
    "T1": "energy_aware",
    "T2": "failure_aware",
    "T3": "mobility_contact_trace",
    "T4": "calibrated_trace_backed",
}


def detect_evaluation_regime(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Detect the evaluation regime and stressor overlays implied by a config."""

    config_dict = dict(config or {})
    declared_regime = _normalize_declared_regime(config_dict.get("evaluation_regime"))
    legacy_declared_tier = _normalize_declared_tier(config_dict.get("realism_tier"))
    overlays = _detect_overlays(config_dict)
    detected_regime = _regime_from_overlays(overlays)
    legacy_detected_tier = _legacy_tier_from_overlays(overlays)

    if declared_regime is None and legacy_declared_tier is not None:
        declared_regime = _legacy_tier_to_regime(legacy_declared_tier)

    return {
        "declared_regime": declared_regime,
        "detected_regime": detected_regime,
        "label": _REGIME_LABELS[detected_regime],
        "overlays": overlays,
        "legacy_realism_tier": {
            "declared_tier": legacy_declared_tier,
            "detected_tier": legacy_detected_tier,
            "label": _TIER_LABELS[legacy_detected_tier],
        },
    }


def detect_realism_tier(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Detect the legacy benchmark realism tier implied by a run config."""

    evaluation = detect_evaluation_regime(config)
    legacy = evaluation["legacy_realism_tier"]
    features = _legacy_features_from_overlays(evaluation["overlays"])
    return {
        "declared_tier": legacy["declared_tier"],
        "detected_tier": legacy["detected_tier"],
        "label": legacy["label"],
        "features": features,
        "evaluation_regime": evaluation["detected_regime"],
        "evaluation_label": evaluation["label"],
    }


def _detect_overlays(config: Mapping[str, Any]) -> Dict[str, bool]:
    """Return evaluation-regime overlays enabled by a config."""

    calibration = _as_mapping(config.get("calibration"))
    infrastructure = _as_mapping(config.get("infrastructure"))
    contact_schedule = _as_mapping(infrastructure.get("contact_schedule"))
    reliability = _as_mapping(config.get("reliability"))
    failures = _as_mapping(reliability.get("failures"))

    contact_mode = str(contact_schedule.get("mode", "")).lower()
    failure_mode = str(failures.get("mode", "disabled")).lower()
    mobility_contact_trace = contact_mode in {
        "trace",
        "trace_file",
        "mobility_trace",
        "contact_trace",
    }
    calibrated_profiles = any(
        key in calibration
        for key in ("network_profile", "service_profile_set")
    )
    calibrated_trace_backed = calibrated_profiles

    return {
        "energy": "energy_profile" in calibration,
        "failures": failure_mode not in {"", "disabled", "none", "off"},
        "contact_mobility": mobility_contact_trace,
        "calibrated_profiles": calibrated_profiles,
        "calibrated_trace_backed": calibrated_trace_backed,
    }


def _regime_from_overlays(overlays: Mapping[str, bool]) -> str:
    """Map overlays to the F0-F2 evaluation-regime axis."""

    if bool(overlays.get("calibrated_trace_backed")):
        return "F2"
    if bool(overlays.get("contact_mobility")):
        return "F1"
    return "F0"


def _legacy_tier_from_overlays(overlays: Mapping[str, bool]) -> str:
    """Map overlays to the legacy T0-T4 realism-tier labels."""

    if bool(overlays.get("calibrated_trace_backed")):
        return "T4"
    if bool(overlays.get("contact_mobility")):
        return "T3"
    if bool(overlays.get("failures")):
        return "T2"
    if bool(overlays.get("energy")):
        return "T1"
    return "T0"


def _legacy_tier_to_regime(tier: str) -> str:
    """Return the F0-F2 regime corresponding to a legacy T0-T4 label."""

    if tier == "T4":
        return "F2"
    if tier == "T3":
        return "F1"
    return "F0"


def _legacy_features_from_overlays(overlays: Mapping[str, bool]) -> Dict[str, bool]:
    """Return the legacy feature names derived from evaluation overlays."""

    return {
        "energy_aware": bool(overlays.get("energy")),
        "failure_aware": bool(overlays.get("failures")),
        "mobility_contact_trace": bool(overlays.get("contact_mobility")),
        "calibrated_trace_backed": bool(overlays.get("calibrated_trace_backed")),
    }


def _normalize_declared_regime(value: Any) -> str | None:
    """Return a normalized F0-F2 evaluation-regime label from config metadata."""

    if value is None:
        return None
    regime = str(value).strip().upper()
    if regime not in _REGIME_LABELS:
        raise ValueError("evaluation_regime must be one of: F0, F1, F2")
    return regime


def _normalize_declared_tier(value: Any) -> str | None:
    """Return a normalized T0-T4 tier label from config metadata."""

    if value is None:
        return None
    tier = str(value).strip().upper()
    if tier not in _TIER_LABELS:
        raise ValueError("realism_tier must be one of: T0, T1, T2, T3, T4")
    return tier


def _as_mapping(value: Any) -> Dict[str, Any]:
    """Return a shallow mapping copy or an empty mapping."""

    if isinstance(value, Mapping):
        return dict(value)
    return {}


__all__ = ["detect_evaluation_regime", "detect_realism_tier"]
