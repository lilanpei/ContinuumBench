from __future__ import annotations

import json
from pathlib import Path

import yaml

from continuum_bench.runner import ContinuumBenchRunner, load_config


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_benchmark_run_writes_machine_readable_decision_trajectory(tmp_path: Path) -> None:
    config = load_config(_repo_root() / "configs" / "eo_small.yaml")
    config["epochs"] = 2
    config["workload"]["streams"]["SpaceSource"]["segments"] = [{"epochs": 2, "rate": 0.0}]
    config["workload"]["streams"]["IoTSource"]["segments"] = [{"epochs": 2, "rate": 0.0}]

    result = ContinuumBenchRunner(
        config=config,
        controller_name="separated_loops",
        seed=7,
        out_root=tmp_path,
    ).run()

    run_dir = Path(result["run_dir"])
    trajectory_path = run_dir / "decision_trajectory.jsonl"

    assert trajectory_path.exists()

    rows = [
        json.loads(line)
        for line in trajectory_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(rows) == config["epochs"]

    for epoch, row in enumerate(rows):
        assert row["epoch"] == epoch
        assert row["time_s"] == epoch * config["step_seconds"]
        assert set(row) == {"epoch", "time_s", "state", "action", "outcome", "reward"}

        assert set(row["state"]) >= {
            "epoch",
            "time_s",
            "seed",
            "placement",
            "queue_depths",
            "oldest_age_s",
            "ttl_pressure",
            "buffer_items",
            "buffer_bytes",
            "worker_pools",
            "metadata",
        }
        assert set(row["state"]["metadata"]) >= {
            "active_services",
            "ready_services",
            "pool_workers",
            "pool_queue_depths",
            "pool_oldest_age_s",
            "pool_ttl_pressure",
        }
        assert isinstance(row["state"]["worker_pools"], dict)

        assert set(row["action"]) == {"scale", "placement", "metadata"}
        assert isinstance(row["action"]["scale"], list)
        assert isinstance(row["action"]["placement"], dict)
        assert isinstance(row["action"]["metadata"], dict)

        assert set(row["outcome"]) >= {
            "epoch",
            "time_s",
            "placement",
            "active_workers",
            "ready_workers",
            "scaling_actions",
            "queues",
            "completions",
            "traffic",
            "buffers",
            "churn",
            "controller_reward",
        }
        assert row["outcome"]["epoch"] == epoch
        assert row["reward"] == row["outcome"]["controller_reward"]


def test_split_manifests_are_parseable_and_scenario_family_aware() -> None:
    root = _repo_root()
    expected = {
        "train": {
            "eo_dual_stream": "earth_observation",
            "industrial_iot": "industrial_iot",
        },
        "val": {
            "driving_v2x": "mobility",
        },
        "test": {
            "split_inference": "vision",
        },
    }

    for split_name, scenario_families in expected.items():
        split_path = root / "splits" / f"{split_name}.yaml"
        payload = yaml.safe_load(split_path.read_text(encoding="utf-8"))

        assert payload["split"] == split_name
        assert isinstance(payload["entries"], list)
        assert payload["entries"]

        actual = {}
        for entry in payload["entries"]:
            assert set(entry) == {"scenario", "family", "config"}
            assert isinstance(entry["scenario"], str) and entry["scenario"]
            assert isinstance(entry["family"], str) and entry["family"]
            assert isinstance(entry["config"], str) and entry["config"]

            config = load_config(root / entry["config"])
            assert config["scenario"] == entry["scenario"]

            actual[entry["scenario"]] = entry["family"]

        assert actual == scenario_families
