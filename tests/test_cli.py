import argparse
import sys
import pytest

from pathlib import Path
from types import SimpleNamespace

from continuum_bench import cli
from continuum_bench.controllers import (
    HPAController,
    KEDAController,
    PlacementOnlyController,
    RLMetaController,
)
from continuum_bench.runner import ContinuumBenchRunner


def test_main_run_dispatches_to_runner_with_documented_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_benchmark(
        config_path: Path,
        controller_name: str,
        seed: int,
        out_root: Path,
    ) -> dict[str, str]:
        captured["config_path"] = config_path
        captured["controller_name"] = controller_name
        captured["seed"] = seed
        captured["out_root"] = out_root
        return {"run_dir": "runs/demo"}

    monkeypatch.setitem(
        sys.modules,
        "continuum_bench.runner",
        SimpleNamespace(run_benchmark=fake_run_benchmark),
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            "cfg.yaml",
            "--controller",
            "round_robin",
            "--seed",
            "7",
            "--out",
            "runs",
        ]
    )

    assert exit_code == 0
    assert captured == {
        "config_path": Path("cfg.yaml"),
        "controller_name": "round_robin",
        "seed": 7,
        "out_root": Path("runs"),
    }


def test_main_summarize_dispatches_to_runner_and_prints_summary_path(
    monkeypatch, capsys
) -> None:
    captured: dict[str, object] = {}

    def fake_summarize_run(run_dir: Path) -> dict[str, object]:
        captured["run_dir"] = run_dir
        return {"status": "ok"}

    monkeypatch.setitem(
        sys.modules,
        "continuum_bench.runner",
        SimpleNamespace(summarize_run=fake_summarize_run),
    )

    exit_code = cli.main(["summarize", "--run_dir", "runs/demo"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert captured == {"run_dir": Path("runs/demo")}
    assert "runs/demo/summary.json" in output.out


def test_main_reports_eclypse_install_hint_for_runtime_commands(
    monkeypatch, capsys
) -> None:
    def raise_missing_eclypse(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'eclypse'", name="eclypse")

    monkeypatch.setitem(
        sys.modules,
        "continuum_bench.runner",
        SimpleNamespace(run_benchmark=raise_missing_eclypse),
    )

    exit_code = cli.main(
        [
            "run",
            "--config",
            "cfg.yaml",
            "--controller",
            "round_robin",
        ]
    )
    output = capsys.readouterr()

    assert exit_code == 1
    assert "Eclypse is required" in output.err
    assert "ECLYPSE_HOME" in output.err


def test_build_parser_documents_extreme_baselines() -> None:
    parser = cli.build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    help_text = subparsers.choices["run"].format_help()

    assert "all_local" in help_text
    assert "all_edge" in help_text
    assert "all_cloud" in help_text
    assert "no_scaling" in help_text
    assert "hpa" in help_text
    assert "keda" in help_text
    assert "learned_stub" in help_text
    assert "rl_meta" in help_text


@pytest.mark.parametrize("controller_name", ["all_local", "all_edge", "all_cloud"])
def test_runner_builds_extreme_baselines_as_placement_only_controllers(
    controller_name: str,
) -> None:
    runner = object.__new__(ContinuumBenchRunner)

    controller = runner._build_controller(controller_name, {"placement": {}, "scaling": {}})

    assert isinstance(controller, PlacementOnlyController)
    assert controller.strategy == controller_name


def test_runner_builds_no_scaling_from_configured_placement_strategy() -> None:
    runner = object.__new__(ContinuumBenchRunner)

    controller = runner._build_controller(
        "no_scaling",
        {"placement": {"strategy": "min_latency", "place_every_epochs": 2}, "scaling": {}},
    )

    assert isinstance(controller, PlacementOnlyController)
    assert controller.strategy == "min_latency"
    assert controller.freeze_after_initial is True


def test_runner_builds_hpa_and_keda_baselines_from_config() -> None:
    runner = object.__new__(ContinuumBenchRunner)

    hpa = runner._build_controller(
        "hpa",
        {
            "placement": {"strategy": "min_latency", "place_every_epochs": 2},
            "autoscaling": {"hpa": {"target_utilization": 0.7, "tolerance": 0.2}},
        },
    )
    keda = runner._build_controller(
        "keda",
        {
            "placement": {"strategy": "best_fit", "place_every_epochs": 3},
            "autoscaling": {
                "keda": {
                    "queue_target_per_worker": 5,
                    "activation_queue_length": 2,
                    "min_replicas": 0,
                    "cooldown_epochs": 4,
                }
            },
        },
    )

    assert isinstance(hpa, HPAController)
    assert hpa.target_utilization == 0.7
    assert hpa.tolerance == 0.2
    assert hpa.min_replicas == 1
    assert isinstance(keda, KEDAController)
    assert keda.queue_target_per_worker == 5
    assert keda.activation_queue_length == 2
    assert keda.cooldown_epochs == 4


def test_runner_builds_rl_meta_controller_from_config() -> None:
    runner = object.__new__(ContinuumBenchRunner)

    controller = runner._build_controller(
        "rl_meta",
        {
            "placement": {"strategy": "best_fit", "place_every_epochs": 5},
            "rl_meta_controller": {
                "policy": "threshold",
                "placement_strategies": ["best_fit", "min_latency"],
                "scale_up_score": 2.5,
                "scale_down_score": 0.2,
                "queue_weight": 2.0,
                "age_weight": 0.0,
                "ttl_weight": 1.5,
                "cooldown_epochs": 3,
            },
        },
    )

    assert isinstance(controller, RLMetaController)
    assert controller.policy == "threshold"
    assert controller.placement_strategies == ("best_fit", "min_latency")
    assert controller.replan_every_epochs == 5
    assert controller.scale_up_score == 2.5
    assert controller.scale_down_score == 0.2
    assert controller.queue_weight == 2.0
    assert controller.age_weight == 0.0
    assert controller.ttl_weight == 1.5
    assert controller.cooldown_epochs == 3
