from __future__ import annotations

import copy

from continuum_bench.runner import ContinuumBenchRunner


def _write_contact_trace(tmp_path) -> str:
    trace_path = tmp_path / "contacts.csv"
    trace_path.write_text(
        "\n".join(
            [
                "start_s,end_s,src,dst,bandwidth_mbps,latency_ms,contact_open",
                "0,2,Space_0,Edge_0,12,80,1",
                "2,4,Space_0,Edge_0,0,80,0",
            ]
        ),
        encoding="utf-8",
    )
    return str(trace_path)


def _config() -> dict:
    return {
        "scenario": "driving_v2x",
        "epochs": 24,
        "step_seconds": 1.0,
        "infrastructure": {
            "infrastructure_id": "repro_infra",
            "n_space": 1,
            "n_iot": 3,
            "n_edge": 2,
            "n_cloud": 1,
            "contact_schedule": {
                "mode": "periodic",
                "period_s": 18,
                "on_duration_s": 6,
                "up_bandwidth_mbps": 40,
                "down_bandwidth_mbps": 0,
            },
        },
        "workload": {
            "streams": {
                "VehicleSource": {
                    "generator": "piecewise",
                    "segments": [
                        {"epochs": 8, "rate": 2.0},
                        {"epochs": 8, "rate": 4.0},
                        {"epochs": 8, "rate": 2.5},
                    ],
                }
            }
        },
        "scaling": {
            "pools": {
                "Offload": {
                    "min_workers": 0,
                    "initial_workers": 1,
                    "max_workers": 3,
                }
            },
            "queue_up_threshold": 10,
            "queue_down_threshold": 3,
            "age_up_threshold_s": 8,
            "age_down_threshold_s": 2,
            "ttl_pressure_up_threshold": 0.2,
            "ttl_pressure_down_threshold": 0.05,
            "cooldown_epochs": 1,
            "scale_step": 1,
        },
        "placement": {
            "strategy": "best_fit",
            "place_every_epochs": 2,
        },
    }


def _normalized_summary(summary: dict) -> dict:
    clean = copy.deepcopy(summary)
    clean.setdefault("metadata", {})
    clean["metadata"].pop("run_id", None)
    clean["metadata"].pop("run_dir", None)
    clean.pop("controller_overhead_ms", None)
    return clean


def test_seeded_runs_are_reproducible(tmp_path):
    config = _config()

    run1 = ContinuumBenchRunner(
        config=copy.deepcopy(config),
        controller_name="separated_loops",
        seed=123,
        out_root=tmp_path / "r1",
    ).run()["summary"]

    run2 = ContinuumBenchRunner(
        config=copy.deepcopy(config),
        controller_name="separated_loops",
        seed=123,
        out_root=tmp_path / "r2",
    ).run()["summary"]

    assert _normalized_summary(run1) == _normalized_summary(run2)


def test_seeded_runs_are_reproducible_with_trace_contact_schedule(tmp_path):
    config = _config()
    config["infrastructure"]["contact_schedule"] = {
        "mode": "trace",
        "path": _write_contact_trace(tmp_path),
        "default_bandwidth_mbps": 0,
    }

    run1 = ContinuumBenchRunner(
        config=copy.deepcopy(config),
        controller_name="separated_loops",
        seed=123,
        out_root=tmp_path / "r1",
    ).run()["summary"]

    run2 = ContinuumBenchRunner(
        config=copy.deepcopy(config),
        controller_name="separated_loops",
        seed=123,
        out_root=tmp_path / "r2",
    ).run()["summary"]

    assert _normalized_summary(run1) == _normalized_summary(run2)
