"""Validate and build calibration profile YAML payloads."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


CALIBRATION_DIR = Path(__file__).resolve().parent
DEFAULT_NETWORK_PROFILES_PATH = CALIBRATION_DIR / "network_profiles.yaml"
DEFAULT_SERVICE_TIME_PROFILES_PATH = CALIBRATION_DIR / "service_time_profiles.yaml"


def _as_mapping(value: Any, context: str) -> dict[str, Any]:
    """Return a YAML value as a plain mapping."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be a YAML mapping")
    return dict(value)


def _require_string(payload: Mapping[str, Any], key: str, context: str) -> str:
    """Return a required non-empty string field."""

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
    """Return a required numeric field with optional bounds checks."""

    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must define numeric '{key}'")

    number = float(value)
    if strictly_positive and number <= 0.0:
        raise ValueError(f"{context} must define positive '{key}'")
    if non_negative and number < 0.0:
        raise ValueError(f"{context} must define non-negative '{key}'")
    return number


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Load a YAML document and require a mapping root."""

    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    return _as_mapping(payload, f"Profile file {path}")


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a YAML mapping to disk."""

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8") as handle:
        yaml.safe_dump(dict(payload), handle, sort_keys=False)


def load_network_profiles(path: Path | None = None) -> dict[str, Any]:
    """Load the network profile document."""

    return _load_yaml_mapping(path or DEFAULT_NETWORK_PROFILES_PATH)


def load_service_time_profiles(path: Path | None = None) -> dict[str, Any]:
    """Load the service-time profile document."""

    return _load_yaml_mapping(path or DEFAULT_SERVICE_TIME_PROFILES_PATH)


def validate_network_profiles(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the network profile payload structure."""

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


def validate_service_time_profiles(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the service-time profile payload structure."""

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


def build_network_profiles_from_measurements(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build validated network profiles from measurement records."""

    profiles: dict[str, Any] = {}

    for index, record in enumerate(records):
        measurement = _as_mapping(record, f"Network measurement record {index}")
        name = _require_string(measurement, "name", f"Network measurement record {index}")
        if name in profiles:
            raise ValueError(f"Duplicate network measurement name '{name}'")

        profiles[name] = {
            "source_tier": _require_string(measurement, "source_tier", name),
            "target_tier": _require_string(measurement, "target_tier", name),
            "regime": _require_string(measurement, "regime", name)
            if "regime" in measurement
            else "measured",
            "latency_ms": {
                "median": _require_number(measurement, "median_rtt_ms", name, strictly_positive=True),
                "p95": _require_number(measurement, "p95_rtt_ms", name, strictly_positive=True),
            },
            "bandwidth_mbps": {
                "sustained": _require_number(
                    measurement,
                    "sustained_mbps",
                    name,
                    strictly_positive=True,
                ),
                "degraded": _require_number(
                    measurement,
                    "degraded_mbps",
                    name,
                    strictly_positive=True,
                ),
            },
            "loss_percent": _require_number(measurement, "loss_percent", name, non_negative=True)
            if "loss_percent" in measurement
            else 0.0,
            "source": _require_string(measurement, "source", name)
            if "source" in measurement
            else "measured",
        }
        if "notes" in measurement:
            profiles[name]["notes"] = _require_string(measurement, "notes", name)

    return validate_network_profiles(
        {
            "schema_version": 1,
            "kind": "network_profiles",
            "profiles": profiles,
        }
    )


def build_service_time_profiles_from_measurements(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build validated service-time profiles from measurement records."""

    workloads: dict[str, Any] = {}
    seen_keys: set[tuple[str, str, str]] = set()

    for index, record in enumerate(records):
        measurement = _as_mapping(record, f"Service measurement record {index}")
        workload = _require_string(measurement, "workload", f"Service measurement record {index}")
        stage = _require_string(measurement, "stage", workload)
        node_class = _require_string(measurement, "node_class", f"{workload}.{stage}")
        key = (workload, stage, node_class)
        if key in seen_keys:
            raise ValueError(
                "Duplicate service measurement key "
                f"'{workload}.{stage}.{node_class}'"
            )
        seen_keys.add(key)

        workloads.setdefault(workload, {}).setdefault(stage, {})[node_class] = {
            "distribution": _require_string(measurement, "distribution", f"{workload}.{stage}.{node_class}")
            if "distribution" in measurement
            else "deterministic",
            "mean_ms": _require_number(
                measurement,
                "mean_ms",
                f"{workload}.{stage}.{node_class}",
                strictly_positive=True,
            ),
            "p95_ms": _require_number(
                measurement,
                "p95_ms",
                f"{workload}.{stage}.{node_class}",
                strictly_positive=True,
            ),
            "source": _require_string(measurement, "source", f"{workload}.{stage}.{node_class}")
            if "source" in measurement
            else "measured",
        }
        if "startup_ms" in measurement:
            workloads[workload][stage][node_class]["startup_ms"] = _require_number(
                measurement,
                "startup_ms",
                f"{workload}.{stage}.{node_class}",
                non_negative=True,
            )
        if "migration_ms" in measurement:
            workloads[workload][stage][node_class]["migration_ms"] = _require_number(
                measurement,
                "migration_ms",
                f"{workload}.{stage}.{node_class}",
                non_negative=True,
            )
        if "notes" in measurement:
            workloads[workload][stage][node_class]["notes"] = _require_string(
                measurement,
                "notes",
                f"{workload}.{stage}.{node_class}",
            )

    return validate_service_time_profiles(
        {
            "schema_version": 1,
            "kind": "service_time_profiles",
            "profiles": workloads,
        }
    )


def _load_records(path: Path) -> list[Mapping[str, Any]]:
    """Load the top-level measurement record list from YAML."""

    payload = _load_yaml_mapping(path)
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"Measurement file {path} must define a 'records' list")
    return records


def main(argv: Sequence[str] | None = None) -> int:
    """Run the calibration profile CLI."""

    parser = argparse.ArgumentParser(
        description="Validate placeholder calibration profiles or build them from simple measurement records.",
    )
    parser.add_argument("kind", choices=["network", "service"])
    parser.add_argument(
        "--measurements",
        type=Path,
        help="Optional YAML file with a top-level 'records' list.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output path. If omitted, the rendered YAML is printed to stdout.",
    )
    args = parser.parse_args(argv)

    if args.kind == "network":
        if args.measurements is None:
            payload = validate_network_profiles(load_network_profiles())
        else:
            payload = build_network_profiles_from_measurements(_load_records(args.measurements))
    else:
        if args.measurements is None:
            payload = validate_service_time_profiles(load_service_time_profiles())
        else:
            payload = build_service_time_profiles_from_measurements(_load_records(args.measurements))

    if args.output is not None:
        _write_yaml(args.output, payload)
    else:
        print(yaml.safe_dump(payload, sort_keys=False), end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
