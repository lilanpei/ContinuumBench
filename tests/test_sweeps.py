from __future__ import annotations

import importlib
from pathlib import Path

import yaml

from continuum_bench.realism import detect_evaluation_regime


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _base_config() -> dict:
    return {
        "scenario": "eo_dual_stream",
        "placement": {
            "strategy": "min_latency",
            "place_every_epochs": 2,
        },
        "scaling": {
            "cooldown_epochs": 2,
        },
    }


def _alt_config() -> dict:
    return {
        "scenario": "split_inference",
        "placement": {
            "strategy": "round_robin",
            "place_every_epochs": 6,
        },
        "scaling": {
            "cooldown_epochs": 9,
        },
    }


def _manifest_payload() -> dict:
    return {
        "name": "demo",
        "config": "configs/base.yaml",
        "seeds": [3, 7],
        "runs": [
            {
                "name": "joint",
                "controller": "separated_loops",
                "overrides": {
                    "placement": {"strategy": "best_fit"},
                    "scaling": {"cooldown_epochs": 5},
                },
            },
            {
                "name": "baseline",
                "controller": "no_scaling",
                "overrides": {
                    "placement": {"place_every_epochs": 4},
                },
            },
        ],
    }


def _write_manifest_fixture(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "configs" / "base.yaml"
    manifest_path = tmp_path / "sweeps" / "demo.yaml"
    _write_yaml(config_path, _base_config())
    _write_yaml(manifest_path, _manifest_payload())
    return manifest_path, config_path


def _write_multi_config_manifest_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    base_config_path = tmp_path / "configs" / "base.yaml"
    alt_config_path = tmp_path / "configs" / "alt.yaml"
    manifest_path = tmp_path / "sweeps" / "demo_multi.yaml"
    payload = _manifest_payload()
    payload["runs"][0]["config"] = "configs/alt.yaml"

    _write_yaml(base_config_path, _base_config())
    _write_yaml(alt_config_path, _alt_config())
    _write_yaml(manifest_path, payload)
    return manifest_path, base_config_path, alt_config_path


def test_sweep_manifest_parses(tmp_path: Path) -> None:
    manifest_path, config_path = _write_manifest_fixture(tmp_path)
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(manifest_path)

    assert manifest.name == "demo"
    assert manifest.manifest_path == manifest_path
    assert manifest.base_config_path == config_path
    assert manifest.seeds == (3, 7)
    assert manifest.overrides == {}
    assert [run.name for run in manifest.runs] == ["joint", "baseline"]
    assert manifest.runs[0].controller == "separated_loops"


def test_runs_expand_deterministically_over_seeds_and_overrides(tmp_path: Path) -> None:
    manifest_path, _ = _write_manifest_fixture(tmp_path)
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(manifest_path)
    specs = manifest_module.expand_runs(manifest)

    assert [(spec.run_name, spec.seed, spec.controller, spec.overrides) for spec in specs] == [
        (
            "joint",
            3,
            "separated_loops",
            {
                "placement": {"strategy": "best_fit"},
                "scaling": {"cooldown_epochs": 5},
            },
        ),
        (
            "joint",
            7,
            "separated_loops",
            {
                "placement": {"strategy": "best_fit"},
                "scaling": {"cooldown_epochs": 5},
            },
        ),
        (
            "baseline",
            3,
            "no_scaling",
            {"placement": {"place_every_epochs": 4}},
        ),
        (
            "baseline",
            7,
            "no_scaling",
            {"placement": {"place_every_epochs": 4}},
        ),
    ]


def test_manifest_level_overrides_apply_before_run_specific_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "configs" / "base.yaml"
    manifest_path = tmp_path / "sweeps" / "demo.yaml"
    payload = _manifest_payload()
    payload["overrides"] = {
        "epochs": 90,
        "drain_epochs": 30,
        "placement": {"place_every_epochs": 5},
    }

    _write_yaml(config_path, _base_config())
    _write_yaml(manifest_path, payload)
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(manifest_path)
    specs = manifest_module.expand_runs(manifest)

    assert manifest.overrides == payload["overrides"]
    assert specs[0].config["epochs"] == 90
    assert specs[0].config["drain_epochs"] == 30
    assert specs[0].config["placement"] == {
        "strategy": "best_fit",
        "place_every_epochs": 5,
    }
    assert specs[2].config["placement"] == {
        "strategy": "min_latency",
        "place_every_epochs": 4,
    }


def test_manifest_override_can_replace_nested_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "configs" / "base.yaml"
    manifest_path = tmp_path / "sweeps" / "demo.yaml"
    payload = _manifest_payload()
    payload["runs"] = [
        {
            "name": "replace_calibration",
            "controller": "separated_loops",
            "overrides": {
                "calibration": {
                    "replace": True,
                    "network_profile": "tracebacked_v1",
                }
            },
        }
    ]
    base = _base_config()
    base["calibration"] = {
        "network_profile": "old_profile",
        "energy_profile": "old_energy",
    }

    _write_yaml(config_path, base)
    _write_yaml(manifest_path, payload)
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(manifest_path)
    spec = manifest_module.expand_runs(manifest)[0]

    assert spec.config["calibration"] == {"network_profile": "tracebacked_v1"}


def test_manifest_contact_repeat_override_only_patches_existing_contact_schedule(
    tmp_path: Path,
) -> None:
    base_config_path = tmp_path / "configs" / "base.yaml"
    trace_config_path = tmp_path / "configs" / "trace.yaml"
    manifest_path = tmp_path / "sweeps" / "demo.yaml"
    base_config = _base_config()
    trace_config = _base_config()
    trace_config["infrastructure"] = {
        "contact_schedule": {
            "mode": "trace",
            "path": "contacts.csv",
        }
    }
    payload = {
        "name": "demo",
        "config": "configs/base.yaml",
        "seeds": [0],
        "overrides": {
            "infrastructure": {
                "contact_schedule": {
                    "repeat": True,
                }
            }
        },
        "runs": [
            {
                "name": "no_contact",
                "controller": "hpa",
            },
            {
                "name": "trace_contact",
                "controller": "hpa",
                "config": "configs/trace.yaml",
            },
        ],
    }

    _write_yaml(base_config_path, base_config)
    _write_yaml(trace_config_path, trace_config)
    _write_yaml(manifest_path, payload)
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    specs = manifest_module.expand_runs(manifest_module.load_manifest(manifest_path))
    by_name = {spec.run_name: spec for spec in specs}

    assert "contact_schedule" not in by_name["no_contact"].config.get("infrastructure", {})
    assert by_name["trace_contact"].config["infrastructure"]["contact_schedule"] == {
        "mode": "trace",
        "path": "contacts.csv",
        "repeat": True,
    }


def test_expanded_run_specs_preserve_scenario_controller_and_config_info(tmp_path: Path) -> None:
    manifest_path, config_path = _write_manifest_fixture(tmp_path)
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(manifest_path)
    spec = manifest_module.expand_runs(manifest)[0]

    assert spec.scenario == "eo_dual_stream"
    assert spec.controller == "separated_loops"
    assert spec.base_config_path == config_path
    assert spec.config["scenario"] == "eo_dual_stream"
    assert spec.config["placement"] == {
        "strategy": "best_fit",
        "place_every_epochs": 2,
    }
    assert spec.config["scaling"] == {"cooldown_epochs": 5}
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == _base_config()


def test_manifest_supports_run_specific_config_paths_deterministically(tmp_path: Path) -> None:
    manifest_path, base_config_path, alt_config_path = _write_multi_config_manifest_fixture(tmp_path)
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(manifest_path)
    specs = manifest_module.expand_runs(manifest)

    assert [(spec.run_name, spec.seed, spec.base_config_path) for spec in specs] == [
        ("joint", 3, alt_config_path),
        ("joint", 7, alt_config_path),
        ("baseline", 3, base_config_path),
        ("baseline", 7, base_config_path),
    ]
    assert [spec.scenario for spec in specs] == [
        "split_inference",
        "split_inference",
        "eo_dual_stream",
        "eo_dual_stream",
    ]
    assert specs[0].config["placement"] == {
        "strategy": "best_fit",
        "place_every_epochs": 6,
    }
    assert specs[0].config["scaling"] == {"cooldown_epochs": 5}
    assert specs[2].config["placement"] == {
        "strategy": "min_latency",
        "place_every_epochs": 4,
    }


def test_run_sweep_materializes_configs_and_calls_run_benchmark(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path, _ = _write_manifest_fixture(tmp_path)
    runner_module = importlib.import_module("continuum_bench.sweeps.runner")

    captured: list[dict[str, object]] = []

    def fake_run_benchmark(
        config_path: Path,
        controller_name: str,
        seed: int,
        out_root: Path,
    ) -> dict[str, object]:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        captured.append(
            {
                "config_path": config_path,
                "controller_name": controller_name,
                "seed": seed,
                "out_root": out_root,
                "scenario": payload["scenario"],
                "placement": payload["placement"],
            }
        )
        return {"run_dir": str(out_root / f"{controller_name}-{seed}"), "summary": {"ok": True}}

    monkeypatch.setattr(runner_module, "run_benchmark", fake_run_benchmark)

    results = runner_module.run_sweep(manifest_path=manifest_path, out_root=tmp_path / "runs")

    assert len(results) == 4
    assert [(call["controller_name"], call["seed"]) for call in captured] == [
        ("separated_loops", 3),
        ("separated_loops", 7),
        ("no_scaling", 3),
        ("no_scaling", 7),
    ]
    assert captured[0]["scenario"] == "eo_dual_stream"
    assert captured[0]["placement"] == {
        "strategy": "best_fit",
        "place_every_epochs": 2,
    }
    assert captured[0]["config_path"] != manifest_path


def test_run_sweep_reports_run_specific_config_provenance(tmp_path: Path, monkeypatch) -> None:
    manifest_path, base_config_path, alt_config_path = _write_multi_config_manifest_fixture(tmp_path)
    runner_module = importlib.import_module("continuum_bench.sweeps.runner")

    captured: list[dict[str, object]] = []

    def fake_run_benchmark(
        config_path: Path,
        controller_name: str,
        seed: int,
        out_root: Path,
    ) -> dict[str, object]:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        captured.append(
            {
                "config_path": config_path,
                "controller_name": controller_name,
                "seed": seed,
                "scenario": payload["scenario"],
            }
        )
        run_dir = out_root / f"{controller_name}-{seed}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return {"run_dir": str(run_dir), "summary": {"ok": True}}

    monkeypatch.setattr(runner_module, "run_benchmark", fake_run_benchmark)

    results = runner_module.run_sweep(manifest_path=manifest_path, out_root=tmp_path / "runs")

    assert [(call["controller_name"], call["seed"], call["scenario"]) for call in captured] == [
        ("separated_loops", 3, "split_inference"),
        ("separated_loops", 7, "split_inference"),
        ("no_scaling", 3, "eo_dual_stream"),
        ("no_scaling", 7, "eo_dual_stream"),
    ]
    assert results[0]["base_config_path"] == str(alt_config_path)
    assert results[2]["base_config_path"] == str(base_config_path)


def test_run_sweep_preserves_per_result_rendered_configs_across_reruns(
    tmp_path: Path, monkeypatch
) -> None:
    manifest_path, _ = _write_manifest_fixture(tmp_path)
    runner_module = importlib.import_module("continuum_bench.sweeps.runner")

    run_counter = {"value": 0}

    def fake_run_benchmark(
        config_path: Path,
        controller_name: str,
        seed: int,
        out_root: Path,
    ) -> dict[str, object]:
        del controller_name, seed, config_path
        run_dir = out_root / f"run_{run_counter['value']}"
        run_counter["value"] += 1
        run_dir.mkdir(parents=True, exist_ok=True)
        return {"run_dir": str(run_dir), "summary": {"ok": True}}

    monkeypatch.setattr(runner_module, "run_benchmark", fake_run_benchmark)

    first_results = runner_module.run_sweep(manifest_path=manifest_path, out_root=tmp_path / "runs")
    first_config_path = Path(first_results[0]["config_path"])
    first_rendered = yaml.safe_load(first_config_path.read_text(encoding="utf-8"))

    updated_manifest = _manifest_payload()
    updated_manifest["runs"][0]["overrides"]["placement"]["strategy"] = "round_robin"
    _write_yaml(manifest_path, updated_manifest)

    second_results = runner_module.run_sweep(manifest_path=manifest_path, out_root=tmp_path / "runs")
    second_config_path = Path(second_results[0]["config_path"])
    second_rendered = yaml.safe_load(second_config_path.read_text(encoding="utf-8"))

    assert first_config_path != second_config_path
    assert yaml.safe_load(first_config_path.read_text(encoding="utf-8")) == first_rendered
    assert first_rendered["placement"]["strategy"] == "best_fit"
    assert second_rendered["placement"]["strategy"] == "round_robin"


def test_nested_manifest_resolves_repo_root_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "configs" / "base.yaml"
    manifest_path = tmp_path / "sweeps" / "v1" / "nested.yaml"
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    _write_yaml(config_path, _base_config())
    _write_yaml(manifest_path, _manifest_payload())

    manifest = manifest_module.load_manifest(manifest_path)

    assert manifest.base_config_path == config_path


def test_main_manifest_is_full_sota_comparable_matrix() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(root / "sweeps" / "main.yaml")
    specs = manifest_module.expand_runs(manifest)

    assert manifest.name == "main"
    assert manifest.seeds == tuple(range(10))
    assert len(manifest.runs) == 72
    assert len(specs) == 720
    assert {spec.scenario for spec in specs} == {
        "eo_dual_stream",
        "driving_v2x",
        "split_inference",
        "industrial_iot",
    }
    assert {spec.controller for spec in specs} == {
        "separated_loops",
        "hpa",
        "keda",
        "best_fit",
        "round_robin",
        "placement_only:cost_greedy",
        "placement_only:milp",
        "learned_stub",
        "rl_meta",
    }
    assert {spec.config["realism_tier"] for spec in specs} == {"T0", "T4"}
    assert {detect_evaluation_regime(spec.config)["detected_regime"] for spec in specs} == {
        "F0",
        "F2",
    }
    assert all(
        detect_evaluation_regime(spec.config)["overlays"]["energy"] is False for spec in specs
    )
    assert {spec.config["epochs"] for spec in specs} == {120}
    assert {spec.config["drain_epochs"] for spec in specs} == {60}
    for spec in specs:
        assert spec.config.get("workload", {}).get("repeat") is True
        contact_schedule = spec.config.get("infrastructure", {}).get("contact_schedule", {})
        if contact_schedule.get("mode") == "trace":
            assert contact_schedule.get("repeat") is True
    driving_t4_specs = [spec for spec in specs if spec.run_name.startswith("driving_t4_")]
    assert driving_t4_specs
    assert all(
        spec.config["calibration"] == {"network_profile": "vehicular_edge_cloud_tracebacked_v1"}
        for spec in driving_t4_specs
    )


def test_realism_ladder_manifest_covers_t0_to_t4_features() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(root / "sweeps" / "realism_ladder.yaml")
    specs = manifest_module.expand_runs(manifest)

    assert manifest.name == "realism_ladder"
    assert manifest.seeds == (0, 1, 2)
    assert len(manifest.runs) == 162
    assert len(specs) == 486
    assert {spec.config["epochs"] for spec in specs} == {120}
    assert {spec.config["drain_epochs"] for spec in specs} == {60}

    by_name = {spec.run_name: spec for spec in specs if spec.seed == 0}
    assert by_name["driving_t0_joint"].config["realism_tier"] == "T0"
    assert detect_evaluation_regime(by_name["driving_t0_joint"].config)["detected_regime"] == "F0"
    assert by_name["driving_t1_joint"].config["calibration"]["energy_profile"] == (
        "v2x_edge_iot_energy_v1"
    )
    assert detect_evaluation_regime(by_name["driving_t1_joint"].config)["detected_regime"] == "F0"
    assert by_name["driving_t2_joint"].config["reliability"]["failures"] == {
        "mode": "bernoulli",
        "node_failure_probability": 0.03,
    }
    assert detect_evaluation_regime(by_name["driving_t2_joint"].config)["detected_regime"] == "F0"
    assert by_name["driving_t3_joint"].config["realism_tier"] == "T3"
    assert by_name["driving_t3_joint"].config.get("calibration") is None
    assert detect_evaluation_regime(by_name["driving_t3_joint"].config)["detected_regime"] == "F1"
    assert by_name["driving_t4_joint"].config["calibration"]["network_profile"] == (
        "vehicular_edge_cloud_tracebacked_v1"
    )
    assert "energy_profile" not in by_name["driving_t4_joint"].config["calibration"]
    assert detect_evaluation_regime(by_name["driving_t4_joint"].config)["detected_regime"] == "F2"
    assert detect_evaluation_regime(by_name["driving_t4_joint"].config)["overlays"]["energy"] is False
    assert by_name["driving_t4_joint"].config["infrastructure"]["contact_schedule"]["repeat"] is True
    assert by_name["split_t4_joint"].config["calibration"]["service_profile_set"] == (
        "split_inference_batch_lab_v1"
    )
    assert "split_t3_joint" not in by_name
    assert "industrial_t3_joint" not in by_name
    assert "contact_schedule" not in by_name["split_t4_joint"].config.get("infrastructure", {})
    assert "contact_schedule" not in by_name["industrial_t4_joint"].config.get(
        "infrastructure", {}
    )


def test_sensitivity_manifests_expand_expected_stress_axes() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    load_manifest = manifest_module.load_manifest(root / "sweeps" / "sensitivity_load.yaml")
    load_specs = manifest_module.expand_runs(load_manifest)
    failure_manifest = manifest_module.load_manifest(
        root / "sweeps" / "sensitivity_failure.yaml"
    )
    failure_specs = manifest_module.expand_runs(failure_manifest)

    assert len(load_manifest.runs) == 72
    assert len(load_specs) == 216
    assert len(failure_manifest.runs) == 72
    assert len(failure_specs) == 216
    assert {spec.config["epochs"] for spec in load_specs} == {120}
    assert {spec.config["drain_epochs"] for spec in load_specs} == {60}
    assert {spec.config["epochs"] for spec in failure_specs} == {120}
    assert {spec.config["drain_epochs"] for spec in failure_specs} == {60}
    assert all(spec.config.get("workload", {}).get("repeat") is True for spec in load_specs)
    assert all(spec.config.get("workload", {}).get("repeat") is True for spec in failure_specs)
    for spec in load_specs:
        contact_schedule = spec.config.get("infrastructure", {}).get("contact_schedule", {})
        if contact_schedule.get("mode") == "trace":
            assert contact_schedule.get("repeat") is True
        if spec.scenario in {"split_inference", "industrial_iot"}:
            assert "contact_schedule" not in spec.config.get("infrastructure", {})
    assert all(
        detect_evaluation_regime(spec.config)["overlays"]["energy"] is False
        for spec in load_specs
    )

    load_names = {spec.run_name for spec in load_specs}
    assert "eo_load_low_joint" in load_names
    assert "eo_load_base_joint" in load_names
    assert "eo_load_high_joint" in load_names
    assert "eo_load_high_rl_meta" in load_names

    eo_low = next(spec for spec in load_specs if spec.run_name == "eo_load_low_joint")
    eo_high = next(spec for spec in load_specs if spec.run_name == "eo_load_high_joint")
    assert eo_low.config["workload"]["streams"]["SpaceSource"]["segments"][1]["rate"] == 1.56
    assert eo_high.config["workload"]["streams"]["SpaceSource"]["segments"][1]["rate"] == 3.64
    driving_load_specs = [
        spec for spec in load_specs if spec.run_name.startswith("driving_load_")
    ]
    assert driving_load_specs
    assert all(
        spec.config["calibration"] == {"network_profile": "vehicular_edge_cloud_tracebacked_v1"}
        for spec in driving_load_specs
    )

    failure_names = {spec.run_name for spec in failure_specs}
    assert "driving_failure_none_joint" in failure_names
    assert "driving_failure_mild_joint" in failure_names
    assert "driving_failure_stress_joint" in failure_names
    assert "driving_failure_stress_rl_meta" in failure_names
    stress = next(spec for spec in failure_specs if spec.run_name == "driving_failure_stress_joint")
    assert stress.config["reliability"]["failures"]["node_failure_probability"] == 0.08


def test_contact_sensitivity_manifest_expands_expected_contact_axis() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(root / "sweeps" / "sensitivity_contact.yaml")
    specs = manifest_module.expand_runs(manifest)

    assert manifest.name == "sensitivity_contact"
    assert manifest.seeds == (0, 1, 2)
    assert len(manifest.runs) == 36
    assert len(specs) == 108
    assert {spec.config["epochs"] for spec in specs} == {120}
    assert {spec.config["drain_epochs"] for spec in specs} == {60}
    assert all(spec.config.get("workload", {}).get("repeat") is True for spec in specs)
    assert all(
        spec.config.get("infrastructure", {}).get("contact_schedule", {}).get("repeat") is True
        for spec in specs
    )

    run_names = {spec.run_name for spec in specs}
    assert "eo_contact_mild_joint" in run_names
    assert "eo_contact_base_joint" in run_names
    assert "eo_contact_stress_joint" in run_names
    assert "driving_contact_stress_rl_meta" in run_names
    assert all(not name.startswith("split_contact_") for name in run_names)
    assert all(not name.startswith("industrial_contact_") for name in run_names)

    eo_mild = next(spec for spec in specs if spec.run_name == "eo_contact_mild_joint")
    eo_stress = next(spec for spec in specs if spec.run_name == "eo_contact_stress_joint")
    driving_stress = next(spec for spec in specs if spec.run_name == "driving_contact_stress_hpa")
    driving_base_static = next(
        spec for spec in specs if spec.run_name == "driving_contact_base_best_fit"
    )
    assert eo_mild.config["infrastructure"]["contact_schedule"]["path"].endswith(
        "eo_space_ground_mild.csv"
    )
    assert eo_stress.config["infrastructure"]["contact_schedule"]["path"].endswith(
        "eo_space_ground_stress.csv"
    )
    assert driving_stress.config["infrastructure"]["contact_schedule"]["path"].endswith(
        "driving_v2x_stress.csv"
    )
    assert driving_stress.config["calibration"] == {
        "network_profile": "vehicular_edge_cloud_tracebacked_v1"
    }
    assert driving_base_static.config["calibration"] == {
        "network_profile": "vehicular_edge_cloud_tracebacked_v1"
    }
    assert detect_evaluation_regime(eo_mild.config)["detected_regime"] == "F2"


def test_sota_pilot_uses_shorter_completion_aware_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(root / "sweeps" / "sota_pilot.yaml")
    specs = manifest_module.expand_runs(manifest)

    assert manifest.name == "sota_pilot"
    assert {spec.config["epochs"] for spec in specs} == {80}
    assert {spec.config["drain_epochs"] for spec in specs} == {40}
    assert all(spec.config.get("workload", {}).get("repeat") is True for spec in specs)
    trace_specs = [
        spec
        for spec in specs
        if spec.config.get("infrastructure", {}).get("contact_schedule", {}).get("mode") == "trace"
    ]
    assert trace_specs
    assert all(
        spec.config["infrastructure"]["contact_schedule"].get("repeat") is True
        for spec in trace_specs
    )


def test_cli_sweep_dispatches_to_batch_runner(monkeypatch) -> None:
    cli = importlib.import_module("continuum_bench.cli")
    captured: dict[str, Path] = {}

    def fake_run_sweep(manifest_path: Path, out_root: Path) -> list[dict[str, object]]:
        captured["manifest_path"] = manifest_path
        captured["out_root"] = out_root
        return [{"run_dir": str(out_root / "demo")}]

    monkeypatch.setattr(cli, "run_sweep", fake_run_sweep, raising=False)

    exit_code = cli.main(["sweep", "--manifest", "sweeps/main.yaml", "--out", "runs"])

    assert exit_code == 0
    assert captured == {
        "manifest_path": Path("sweeps/main.yaml"),
        "out_root": Path("runs"),
    }


def test_run_overrides_deliver_unknown_top_level_block(tmp_path: Path) -> None:
    # A top-level block the manifest schema does not know about (here a controller's own
    # settings block) must be deep-merged into the expanded run config untouched, alongside
    # the overrides that do target known sections.
    config_path = tmp_path / "configs" / "base.yaml"
    manifest_path = tmp_path / "sweeps" / "custom_controller.yaml"
    _write_yaml(config_path, _base_config())
    payload = {
        "name": "custom_controller",
        "config": "configs/base.yaml",
        "seeds": [5, 6],
        "runs": [
            {
                "name": "custom_contact",
                "controller": "hpa",
                "overrides": {
                    "placement": {"strategy": "min_latency", "place_every_epochs": 2},
                    "custom_controller": {
                        "history_limit": 3,
                        "max_length": 5120,
                    },
                },
            }
        ],
    }
    _write_yaml(manifest_path, payload)
    manifest_module = importlib.import_module("continuum_bench.sweeps.manifest")

    manifest = manifest_module.load_manifest(manifest_path)
    specs = manifest_module.expand_runs(manifest)

    assert [spec.seed for spec in specs] == [5, 6]
    for spec in specs:
        assert spec.controller == "hpa"
        # the unknown block rides intact into the run config (deep-merge preserves it)...
        custom = spec.config["custom_controller"]
        assert custom["history_limit"] == 3
        assert custom["max_length"] == 5120
        # ...and the base config's placement is overridden to the run's values.
        assert spec.config["placement"]["strategy"] == "min_latency"
        assert spec.config["placement"]["place_every_epochs"] == 2
