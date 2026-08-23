from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from continuum_bench.runner import load_config


@dataclass(frozen=True)
class SweepRun:
    name: str
    controller: str
    config_path: Path | None
    overrides: dict[str, Any]


@dataclass(frozen=True)
class SweepManifest:
    name: str
    manifest_path: Path
    base_config_path: Path
    seeds: tuple[int, ...]
    overrides: dict[str, Any]
    runs: tuple[SweepRun, ...]
    base_config: dict[str, Any]


@dataclass(frozen=True)
class RunSpec:
    sweep_name: str
    run_name: str
    controller: str
    seed: int
    scenario: str
    base_config_path: Path
    overrides: dict[str, Any]
    config: dict[str, Any]


def load_manifest(manifest_path: Path) -> SweepManifest:
    """Load and validate a sweep manifest file."""

    path = Path(manifest_path)
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)

    if not isinstance(payload, dict):
        raise ValueError("Sweep manifest must be a YAML mapping")

    name = str(payload["name"])
    base_config_path = _resolve_manifest_path(path, str(payload["config"]))
    base_config = load_config(base_config_path)
    seeds = tuple(int(seed) for seed in payload["seeds"])
    overrides = payload.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("Sweep manifest overrides must be a YAML mapping")
    runs = tuple(_parse_run(item, manifest_path=path) for item in payload["runs"])

    return SweepManifest(
        name=name,
        manifest_path=path,
        base_config_path=base_config_path,
        seeds=seeds,
        overrides=deepcopy(overrides),
        runs=runs,
        base_config=base_config,
    )


def expand_runs(manifest: SweepManifest) -> list[RunSpec]:
    """Expand a sweep manifest into concrete run specifications."""

    specs: list[RunSpec] = []
    base_configs: dict[Path, dict[str, Any]] = {manifest.base_config_path: manifest.base_config}
    for run in manifest.runs:
        base_config_path = run.config_path or manifest.base_config_path
        base_config = base_configs.get(base_config_path)
        if base_config is None:
            base_config = load_config(base_config_path)
            base_configs[base_config_path] = base_config
        for seed in manifest.seeds:
            config = _deep_merge(_deep_merge(base_config, manifest.overrides), run.overrides)
            specs.append(
                RunSpec(
                    sweep_name=manifest.name,
                    run_name=run.name,
                    controller=run.controller,
                    seed=seed,
                    scenario=str(config.get("scenario", "")),
                    base_config_path=base_config_path,
                    overrides=deepcopy(run.overrides),
                    config=config,
                )
            )
    return specs


def _parse_run(payload: Any, manifest_path: Path) -> SweepRun:
    """Parse one run entry from a sweep manifest."""

    if not isinstance(payload, dict):
        raise ValueError("Each sweep run must be a YAML mapping")
    overrides = payload.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("Sweep run overrides must be a YAML mapping")
    raw_config_path = payload.get("config")
    return SweepRun(
        name=str(payload["name"]),
        controller=str(payload["controller"]),
        config_path=(
            _resolve_manifest_path(manifest_path, str(raw_config_path))
            if raw_config_path is not None
            else None
        ),
        overrides=deepcopy(overrides),
    )


def _resolve_manifest_path(manifest_path: Path, raw_path: str) -> Path:
    """Resolve a manifest-relative path with a repo-root fallback."""

    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate

    relative_candidate = (manifest_path.parent / candidate).resolve()
    if relative_candidate.exists():
        return relative_candidate

    repo_root = _infer_repo_root(manifest_path)
    return (repo_root / candidate).resolve()


def _infer_repo_root(manifest_path: Path) -> Path:
    """Infer the repository root from a path under `sweeps/`."""

    for parent in manifest_path.resolve().parents:
        if parent.name == "sweeps":
            return parent.parent
    return manifest_path.parent


def _deep_merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge override values into a copied config."""

    merged = deepcopy(base)
    for key, value in overrides.items():
        existing = merged.get(key)
        if key == "infrastructure" and existing is None and _is_infrastructure_contact_patch(value):
            continue
        if key == "contact_schedule" and existing is None and _is_contact_schedule_patch(value):
            continue
        if isinstance(value, Mapping) and bool(value.get("replace", False)):
            replacement = {
                nested_key: deepcopy(nested_value)
                for nested_key, nested_value in value.items()
                if nested_key != "replace"
            }
            merged[key] = replacement
            continue
        if isinstance(existing, dict) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
            continue
        merged[key] = deepcopy(value)
    return merged


def _is_contact_schedule_patch(value: Any) -> bool:
    """Return true when an override only patches an existing contact schedule."""

    if not isinstance(value, Mapping):
        return False
    return set(value).issubset({"repeat", "repeat_trace", "symmetric"})


def _is_infrastructure_contact_patch(value: Any) -> bool:
    """Return true when an infrastructure override only patches contact settings."""

    if not isinstance(value, Mapping):
        return False
    return set(value) == {"contact_schedule"} and _is_contact_schedule_patch(
        value.get("contact_schedule")
    )


__all__ = [
    "RunSpec",
    "SweepManifest",
    "SweepRun",
    "expand_runs",
    "load_manifest",
]
