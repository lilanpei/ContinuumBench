"""Validate controller ranking payloads against calibration inputs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibration.fit_profiles import (  # noqa: E402
    load_network_profiles,
    load_service_time_profiles,
    validate_network_profiles,
    validate_service_time_profiles,
)


REQUIRED_RANKING_FIELDS = ["controller", "rank"]


def _as_mapping(value: Any, context: str) -> dict[str, Any]:
    """Return a value as a plain mapping."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a mapping")
    return dict(value)


def _require_string(payload: Mapping[str, Any], key: str, context: str) -> str:
    """Return a required non-empty string field."""

    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} must define a non-empty '{key}' string")
    return value.strip()


def _require_rank(payload: Mapping[str, Any], context: str) -> int:
    """Return a required positive ranking value."""

    value = payload.get("rank")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{context} must define a positive integer 'rank'")
    return value


def _load_yaml_document(path: Path) -> Any:
    """Load a YAML document from disk."""

    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _count_service_profiles(service_time_profiles: Mapping[str, Any]) -> int:
    """Count leaf service-time profiles across workloads and stages."""

    total = 0
    for workload in service_time_profiles["profiles"].values():
        for stage in workload.values():
            total += len(stage)
    return total


def _controller_names(rankings: Mapping[str, Any]) -> set[str]:
    """Return the controller names present in a rankings payload."""

    return {entry["controller"] for entry in rankings["rankings"]}


def validate_rankings_payload(payload: Any, *, label: str = "Rankings") -> dict[str, Any]:
    """Validate a controller rankings payload."""

    if isinstance(payload, list):
        root = {
            "schema_version": 1,
            "kind": "controller_rankings",
            "rankings": payload,
        }
    else:
        root = _as_mapping(payload, label)

    schema_version = int(root.get("schema_version", 1))
    if schema_version != 1:
        raise ValueError(f"{label} must declare schema_version: 1")

    kind = root.get("kind", "controller_rankings")
    if kind != "controller_rankings":
        raise ValueError(f"{label} must declare kind: controller_rankings")

    rankings = root.get("rankings")
    if not isinstance(rankings, list):
        raise ValueError(f"{label} must define a 'rankings' list")

    seen_controllers: set[str] = set()
    for index, raw_entry in enumerate(rankings):
        entry = _as_mapping(raw_entry, f"{label} entry {index}")
        controller = _require_string(entry, "controller", f"{label} entry {index}")
        _require_rank(entry, f"{label} entry {index}")
        if controller in seen_controllers:
            raise ValueError(f"{label} contains duplicate controller '{controller}'")
        seen_controllers.add(controller)

    return root


def load_rankings(path: Path) -> dict[str, Any]:
    """Load and validate a rankings document."""

    return validate_rankings_payload(_load_yaml_document(path), label=f"Rankings file {path}")


def build_validation_scaffold(
    *,
    network_profiles: Mapping[str, Any] | Path | None = None,
    service_time_profiles: Mapping[str, Any] | Path | None = None,
    sim_rankings: Mapping[str, Any] | Sequence[Mapping[str, Any]] | Path | None = None,
    real_rankings: Mapping[str, Any] | Sequence[Mapping[str, Any]] | Path | None = None,
) -> dict[str, Any]:
    """Build a comparison-readiness report for calibration inputs."""

    if network_profiles is None:
        validated_network_profiles = validate_network_profiles(load_network_profiles())
    elif isinstance(network_profiles, Path):
        validated_network_profiles = validate_network_profiles(load_network_profiles(network_profiles))
    else:
        validated_network_profiles = validate_network_profiles(network_profiles)

    if service_time_profiles is None:
        validated_service_time_profiles = validate_service_time_profiles(load_service_time_profiles())
    elif isinstance(service_time_profiles, Path):
        validated_service_time_profiles = validate_service_time_profiles(
            load_service_time_profiles(service_time_profiles)
        )
    else:
        validated_service_time_profiles = validate_service_time_profiles(service_time_profiles)

    missing_inputs: list[str] = []
    validated_sim_rankings = None
    validated_real_rankings = None

    if sim_rankings is None:
        missing_inputs.append("sim_rankings")
    elif isinstance(sim_rankings, Path):
        validated_sim_rankings = load_rankings(sim_rankings)
    else:
        validated_sim_rankings = validate_rankings_payload(sim_rankings, label="sim_rankings")

    if real_rankings is None:
        missing_inputs.append("real_rankings")
    elif isinstance(real_rankings, Path):
        validated_real_rankings = load_rankings(real_rankings)
    else:
        validated_real_rankings = validate_rankings_payload(real_rankings, label="real_rankings")

    report = {
        "status": "pending_measurements" if missing_inputs else "ready_for_comparison",
        "calibration_ready": True,
        "missing_inputs": missing_inputs,
        "network_profile_count": len(validated_network_profiles["profiles"]),
        "service_profile_count": _count_service_profiles(validated_service_time_profiles),
        "required_ranking_fields": list(REQUIRED_RANKING_FIELDS),
    }

    if validated_sim_rankings is not None:
        report["sim_controller_count"] = len(validated_sim_rankings["rankings"])
    if validated_real_rankings is not None:
        report["real_controller_count"] = len(validated_real_rankings["rankings"])
    if validated_sim_rankings is not None and validated_real_rankings is not None:
        sim_controllers = _controller_names(validated_sim_rankings)
        real_controllers = _controller_names(validated_real_rankings)
        report["controller_overlap"] = sorted(sim_controllers & real_controllers)
        report["sim_only_controllers"] = sorted(sim_controllers - real_controllers)
        report["real_only_controllers"] = sorted(real_controllers - sim_controllers)
        if sim_controllers != real_controllers:
            report["status"] = "controller_set_mismatch"
            report["calibration_ready"] = False

    return report


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ranking validation command-line entry point."""

    parser = argparse.ArgumentParser(
        description="Validate calibration profiles and future simulator-vs-real controller rankings.",
    )
    parser.add_argument("--network-profiles", type=Path, default=None)
    parser.add_argument("--service-time-profiles", type=Path, default=None)
    parser.add_argument("--sim-rankings", type=Path, default=None)
    parser.add_argument("--real-rankings", type=Path, default=None)
    args = parser.parse_args(argv)

    report = build_validation_scaffold(
        network_profiles=args.network_profiles,
        service_time_profiles=args.service_time_profiles,
        sim_rankings=args.sim_rankings,
        real_rankings=args.real_rankings,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
