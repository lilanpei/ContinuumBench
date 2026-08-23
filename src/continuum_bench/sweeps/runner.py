from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from continuum_bench.runner import run_benchmark
from continuum_bench.sweeps.manifest import RunSpec, expand_runs, load_manifest


def run_sweep(manifest_path: Path, out_root: Path) -> list[dict[str, Any]]:
    """Run every expanded sweep spec and collect their results."""

    manifest = load_manifest(manifest_path)
    specs = expand_runs(manifest)

    results: list[dict[str, Any]] = []
    for index, spec in enumerate(specs):
        staging_config_path = _write_staging_config(spec, Path(out_root), index)
        result = run_benchmark(
            config_path=staging_config_path,
            controller_name=spec.controller,
            seed=spec.seed,
            out_root=Path(out_root),
        )
        rendered_config_path = _attach_config_to_run_dir(staging_config_path, Path(result["run_dir"]))
        results.append(
            {
                "sweep": spec.sweep_name,
                "run_name": spec.run_name,
                "scenario": spec.scenario,
                "controller": spec.controller,
                "seed": spec.seed,
                "base_config_path": str(spec.base_config_path),
                "config_path": str(rendered_config_path),
                **result,
            }
        )
    return results


def _write_staging_config(spec: RunSpec, out_root: Path, index: int) -> Path:
    """Write the rendered config used for one staged sweep run."""

    config_root = out_root / ".sweep_staging" / spec.sweep_name
    config_root.mkdir(parents=True, exist_ok=True)
    config_path = config_root / f"{index:03d}_{_slug(spec.run_name)}_seed{spec.seed}.yaml"
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(spec.config, handle, sort_keys=False)
    return config_path


def _attach_config_to_run_dir(staging_config_path: Path, run_dir: Path) -> Path:
    """Copy the staged config into the run provenance directory."""

    provenance_dir = run_dir / "provenance"
    provenance_dir.mkdir(parents=True, exist_ok=True)
    rendered_config_path = provenance_dir / "config.yaml"
    shutil.copy2(staging_config_path, rendered_config_path)
    return rendered_config_path


def _slug(value: str) -> str:
    """Convert a run name into a filesystem-safe slug."""

    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return slug or "run"


__all__ = ["run_sweep"]
