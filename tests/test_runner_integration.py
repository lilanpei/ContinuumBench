from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest

import continuum_bench.workload.mmpp as mmpp_module
from continuum_bench.controllers import ControllerAction, ScaleAction
from continuum_bench.metrics import summarize_epochs
from continuum_bench.runner import ContinuumBenchRunner, InFlightMessage, Task, load_config
from continuum_bench.runtime.store_forward import BufferPriority


def _scipy_milp_available() -> bool:
    try:
        from scipy.optimize import milp  # noqa: F401
    except Exception:
        return False
    return True


SCIPY_MILP_AVAILABLE = _scipy_milp_available()


def _runner_config(
    *,
    initial_workers: int,
    max_workers: int = 2,
    startup_delay_s: float = 0.0,
    migration_delay_s: float = 0.0,
) -> dict:
    return {
        "scenario": "eo_dual_stream",
        "epochs": 8,
        "step_seconds": 1.0,
        "infrastructure": {
            "infrastructure_id": "runner_integration",
            "n_space": 1,
            "n_iot": 2,
            "n_edge": 2,
            "n_cloud": 2,
            "contact_schedule": {
                "mode": "periodic",
                "period_s": 200,
                "on_duration_s": 200,
                "up_bandwidth_mbps": 80,
                "down_bandwidth_mbps": 0,
            },
        },
        "stages": {
            "SpaceSource": {"priority_prob_high": 0.0, "output_size_bytes": 64},
            "IoTSource": {"priority_prob_high": 0.0, "output_size_bytes": 64},
            "BulkPathWorker": {"processing_time_s": 1.0},
        },
        "workload": {
            "streams": {
                "SpaceSource": {
                    "generator": "piecewise",
                    "segments": [{"epochs": 8, "rate": 0.0}],
                },
                "IoTSource": {
                    "generator": "piecewise",
                    "segments": [{"epochs": 8, "rate": 0.0}],
                },
            }
        },
        "scaling": {
            "pools": {
                "BulkPath": {
                    "min_workers": 0,
                    "initial_workers": initial_workers,
                    "max_workers": max_workers,
                    "startup_delay_s": startup_delay_s,
                }
            }
        },
        "placement": {
            "strategy": "best_fit",
            "place_every_epochs": 1,
            "migration_delay_s": migration_delay_s,
        },
    }


def _build_runner(
    tmp_path,
    *,
    initial_workers: int,
    max_workers: int = 2,
    startup_delay_s: float = 0.0,
    migration_delay_s: float = 0.0,
) -> ContinuumBenchRunner:
    return ContinuumBenchRunner(
        config=_runner_config(
            initial_workers=initial_workers,
            max_workers=max_workers,
            startup_delay_s=startup_delay_s,
            migration_delay_s=migration_delay_s,
        ),
        controller_name="separated_loops",
        seed=0,
        out_root=tmp_path,
    )


def _stage_id(runner: ContinuumBenchRunner, stage_name: str) -> str:
    for service_id, attrs in runner.application.nodes.items():
        if attrs.get("stage_name") == stage_name:
            return service_id
    raise AssertionError(f"Missing stage {stage_name}")


def _task_count(runner: ContinuumBenchRunner) -> int:
    queued = sum(len(runtime.queue) for runtime in runner.stage_runtimes.values())
    buffered = runner._aggregate_buffer_snapshots()["items"]
    return queued + buffered + len(runner.inflight)


def _first_node_id(runner: ContinuumBenchRunner) -> str:
    nodes = getattr(runner.infrastructure, "available", runner.infrastructure).nodes
    if hasattr(nodes, "keys"):
        return next(iter(nodes.keys()))
    return next(iter(nodes))


def _node_ids(runner: ContinuumBenchRunner, count: int) -> list[str]:
    nodes = getattr(runner.infrastructure, "available", runner.infrastructure).nodes
    if hasattr(nodes, "keys"):
        return list(nodes.keys())[:count]
    return list(nodes)[:count]


def _node_id_for_layer(runner: ContinuumBenchRunner, layer: str) -> str:
    nodes = getattr(runner.infrastructure, "available", runner.infrastructure).nodes
    items = nodes.items() if hasattr(nodes, "items") else ((node_id, nodes[node_id]) for node_id in nodes)
    for node_id, attrs in items:
        placement_layers = dict(attrs).get("placement_layers", [])
        if isinstance(placement_layers, list) and [str(item).lower() for item in placement_layers] == [layer]:
            return node_id
    raise AssertionError(f"Missing node in layer {layer}")


def _relax_all_node_capacities(
    runner: ContinuumBenchRunner,
    *,
    cpu: float = 128.0,
    ram: float = 1024.0,
    storage: float = 10000.0,
) -> None:
    nodes = getattr(runner.infrastructure, "nodes", {})
    items = nodes.items() if hasattr(nodes, "items") else ((node_id, nodes[node_id]) for node_id in nodes)
    for _node_id, attrs in items:
        attrs.update({"cpu": cpu, "ram": ram, "storage": storage})


def _establish_initial_placement(
    runner: ContinuumBenchRunner,
    *,
    epoch: int = 0,
) -> ControllerAction:
    action = runner.controller.step(
        runner._build_controller_state(
            epoch=epoch,
            time_s=float(epoch) * runner.step_seconds,
        )
    )
    assert action.placement
    runner.placement.update(action.placement)
    runner._validate_placement_for_active_services()
    return action


def _freeze_existing_placement(
    runner: ContinuumBenchRunner,
    monkeypatch,
    *,
    epoch: int = 0,
) -> None:
    placement_controller = runner._build_controller("placement_only:best_fit", runner.config)
    action = placement_controller.step(
        runner._build_controller_state(
            epoch=epoch,
            time_s=float(epoch) * runner.step_seconds,
        )
    )
    assert action.placement
    for service_id, node_id in action.placement.items():
        runner.placement.setdefault(service_id, node_id)
    runner._validate_placement_for_active_services()
    monkeypatch.setattr(runner.controller, "step", lambda _state: ControllerAction())


class _StaticStream:
    def __init__(self, count: int):
        self.count = int(count)

    def sample(self, epoch: int) -> int:
        return self.count if epoch == 0 else 0


class _ConstantStream:
    def __init__(self, count: int):
        self.count = int(count)
        self.epochs: list[int] = []

    def sample(self, epoch: int) -> int:
        self.epochs.append(int(epoch))
        return self.count


def test_no_scaling_uses_configured_initial_anchored_placement_and_freezes_it(
    tmp_path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs" / "industrial_iot_small.yaml")
    config["epochs"] = 2

    runner = ContinuumBenchRunner(
        config=config,
        controller_name="no_scaling",
        seed=0,
        out_root=tmp_path,
    )

    record_epoch_0 = runner._run_epoch(0)
    record_epoch_1 = runner._run_epoch(1)

    assert record_epoch_0["scaling_actions"] == []
    assert record_epoch_1["scaling_actions"] == []
    assert record_epoch_1["placement"] == record_epoch_0["placement"]
    assert record_epoch_1["active_workers"] == record_epoch_0["active_workers"]

    for service_id, node_id in record_epoch_0["placement"].items():
        placement_layers = runner.application.nodes[service_id].get("placement_layers", [])
        if not placement_layers:
            continue
        assert dict(runner.infrastructure.nodes[node_id]).get("placement_layers") == placement_layers


@pytest.mark.skipif(not SCIPY_MILP_AVAILABLE, reason="scipy MILP backend is not installed")
def test_milp_placement_controller_returns_auditable_mapping(tmp_path) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    controller = runner._build_controller("placement_only:milp", runner.config)

    action = controller.step(runner._build_controller_state(epoch=0, time_s=0.0))

    assert action.placement
    runner.placement.update(action.placement)
    runner._validate_placement_for_active_services()


@pytest.mark.skipif(not SCIPY_MILP_AVAILABLE, reason="scipy MILP backend is not installed")
def test_milp_placement_controller_logs_initial_failure_outage(tmp_path) -> None:
    config = _runner_config(initial_workers=1, max_workers=1)
    config["reliability"] = {
        "failures": {
            "mode": "deterministic",
            "events": [
                {
                    "epoch": 0,
                    "node_down": [
                        "Space_0",
                        "IoT_0",
                        "IoT_1",
                        "Edge_0",
                        "Edge_1",
                        "Cloud_0",
                        "Cloud_1",
                    ],
                }
            ],
        }
    }
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="placement_only:milp",
        seed=0,
        out_root=tmp_path,
    )

    outage_record = runner._run_epoch(0)
    recovery_record = runner._run_epoch(1)

    assert outage_record["placement"] == {}
    assert outage_record["placement_outage"] is True
    assert recovery_record["placement"]
    assert recovery_record["placement_outage"] is False


def test_runner_rejects_missing_controller_placement_instead_of_repairing_locally(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    active_service = runner.worker_pool_manager.active_service_ids()[0]
    node_id = _first_node_id(runner)
    runner.placement = {active_service: node_id}

    monkeypatch.setattr(runner.controller, "step", lambda _state: ControllerAction())

    with pytest.raises(ValueError, match="missing placements"):
        runner._run_epoch(0)


def test_initial_failure_outage_is_logged_and_retried_without_crashing(tmp_path) -> None:
    config = _runner_config(initial_workers=1, max_workers=1)
    config["reliability"] = {
        "failures": {
            "mode": "deterministic",
            "events": [
                {
                    "epoch": 0,
                    "node_down": ["Space_0", "IoT_0", "IoT_1", "Edge_0", "Edge_1", "Cloud_0", "Cloud_1"],
                }
            ],
        }
    }
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="separated_loops",
        seed=0,
        out_root=tmp_path,
    )

    outage_record = runner._run_epoch(0)
    recovery_record = runner._run_epoch(1)

    assert outage_record["placement"] == {}
    assert outage_record["placement_outage"] is True
    assert outage_record["failures"]["node_down"]
    assert outage_record["reliability"]["failure_event_failed_tasks"] >= 0
    assert recovery_record["placement"]
    assert recovery_record["placement_outage"] is False


def test_worker_startup_delay_prevents_immediate_processing_after_scale_up(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(
        tmp_path,
        initial_workers=0,
        max_workers=1,
        startup_delay_s=2.0,
    )
    worker_id = runner.scenario.worker_pools["BulkPath"][0]

    runner.worker_pool_manager.set_active_workers("BulkPath", 1)
    runner.stage_runtimes[worker_id].queue.append(
        Task(
            task_id="startup-delayed-task",
            source_id=worker_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )
    _freeze_existing_placement(runner, monkeypatch)

    record_epoch_0 = runner._run_epoch(0)
    record_epoch_1 = runner._run_epoch(1)
    record_epoch_2 = runner._run_epoch(2)

    assert record_epoch_0["queues"][worker_id] == 1
    assert record_epoch_1["queues"][worker_id] == 1
    assert record_epoch_2["queues"][worker_id] == 0


def test_routing_targets_ready_workers_not_active_but_starting_workers(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(
        tmp_path,
        initial_workers=1,
        max_workers=2,
        startup_delay_s=2.0,
    )
    triage_id = _stage_id(runner, "Triage")
    ready_worker_id = runner.scenario.worker_pools["BulkPath"][0]
    starting_worker_id = runner.scenario.worker_pools["BulkPath"][1]

    runner.worker_pool_manager.set_active_workers("BulkPath", 2)

    sent_targets: list[str] = []

    def record_send(src_stage: str, dst_stage: str, task: Task, epoch: int) -> None:
        del src_stage, task, epoch
        sent_targets.append(dst_stage)

    monkeypatch.setattr(runner, "_send_or_buffer", record_send)
    runner.stage_runtimes[triage_id].route_rr_index = 1

    runner._route_task(
        triage_id,
        Task(
            task_id="route-ready-only",
            source_id=triage_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        ),
        epoch=0,
    )

    assert sent_targets == [ready_worker_id]
    assert starting_worker_id not in sent_targets


def test_fully_cold_pool_preserves_low_priority_work_upstream_until_ready(
    tmp_path,
) -> None:
    runner = _build_runner(
        tmp_path,
        initial_workers=0,
        max_workers=1,
        startup_delay_s=2.0,
    )
    triage_id = _stage_id(runner, "Triage")
    worker_id = runner.scenario.worker_pools["BulkPath"][0]
    node_id = _first_node_id(runner)

    runner.worker_pool_manager.set_active_workers("BulkPath", 1)
    runner.placement[triage_id] = node_id
    runner.placement[worker_id] = node_id

    runner._route_task(
        triage_id,
        Task(
            task_id="cold-pool-low-priority",
            source_id=triage_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        ),
        epoch=0,
    )

    assert len(runner.inflight) == 0
    assert runner.stage_runtimes[triage_id].buffers[worker_id].size == 1


def test_recovered_cold_pool_backlog_is_redistributed_across_ready_workers(
    tmp_path,
) -> None:
    runner = _build_runner(
        tmp_path,
        initial_workers=0,
        max_workers=2,
        startup_delay_s=2.0,
    )
    triage_id = _stage_id(runner, "Triage")
    worker_ids = runner.scenario.worker_pools["BulkPath"][:2]
    node_id = _first_node_id(runner)

    runner.placement[triage_id] = node_id
    for worker_id in worker_ids:
        runner.placement[worker_id] = node_id

    runner.worker_pool_manager.set_active_workers("BulkPath", 2)
    for task_id in ["cold-1", "cold-2"]:
        runner._route_task(
            triage_id,
            Task(
                task_id=task_id,
                source_id=triage_id,
                created_epoch=0,
                created_time_s=0.0,
                priority="low",
                size_bytes=32,
            ),
            epoch=0,
        )

    assert runner.stage_runtimes[triage_id].buffers[worker_ids[0]].size == 2

    runner.clock.now_s = 2.0
    runner._flush_buffers(epoch=2)
    delivered_targets = sorted(message.target_stage for message in runner.inflight)

    assert delivered_targets == sorted(worker_ids)


def test_non_integral_startup_delay_uses_epoch_quantized_overhead(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(
        tmp_path,
        initial_workers=0,
        max_workers=1,
        startup_delay_s=0.2,
    )
    worker_id = runner.scenario.worker_pools["BulkPath"][0]

    runner.worker_pool_manager.set_active_workers("BulkPath", 1)
    runner.stage_runtimes[worker_id].queue.append(
        Task(
            task_id="quantized-startup-task",
            source_id=worker_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )
    _freeze_existing_placement(runner, monkeypatch)

    record_epoch_0 = runner._run_epoch(0)
    record_epoch_1 = runner._run_epoch(1)

    assert record_epoch_0["churn"]["startup_delay_s"] == 1.0
    assert record_epoch_0["queues"][worker_id] == 1
    assert record_epoch_1["queues"][worker_id] == 0


def test_run_epoch_reports_migration_delay_signal_when_placement_changes(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(
        tmp_path,
        initial_workers=1,
        max_workers=1,
        migration_delay_s=3.0,
    )
    worker_id = runner.worker_pool_manager.active_workers("BulkPath")[0]
    cloud_nodes = [
        node_id
        for node_id, attrs in runner.infrastructure.nodes.items()
        if dict(attrs).get("placement_layers") == ["cloud"]
    ]
    node_a, node_b = cloud_nodes[:2]

    _freeze_existing_placement(runner, monkeypatch)
    runner.placement[worker_id] = node_a
    monkeypatch.setattr(
        runner.controller,
        "step",
        lambda _state: ControllerAction(placement={worker_id: node_b}),
    )

    record = runner._run_epoch(0)

    assert record["churn"]["migrations"] == 1.0
    assert record["churn"]["migration_delay_s"] == 3.0


def test_low_priority_task_for_zero_worker_pool_is_preserved(tmp_path) -> None:
    runner = _build_runner(tmp_path, initial_workers=0, max_workers=1)
    triage_id = _stage_id(runner, "Triage")
    worker_id = runner.scenario.worker_pools["BulkPath"][0]

    task = Task(
        task_id="task-low",
        source_id=triage_id,
        created_epoch=0,
        created_time_s=0.0,
        priority="low",
        size_bytes=64,
    )

    runner.clock.now_s = 0.0
    runner._route_task(triage_id, task, epoch=0)

    assert _task_count(runner) == 1

    node_id = _first_node_id(runner)
    runner.worker_pool_manager.set_active_workers("BulkPath", 1)
    runner.placement[triage_id] = node_id
    runner.placement[worker_id] = node_id

    runner._flush_buffers(epoch=0)
    runner._deliver_inflight(epoch=1)

    assert len(runner.stage_runtimes[worker_id].queue) == 1


def test_build_controller_state_surfaces_pool_signals_in_primary_fields(tmp_path) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=2)
    worker_id = runner.worker_pool_manager.active_workers("BulkPath")[0]
    sink_id = next(iter(runner.stage_runtimes[worker_id].buffers))

    runner.stage_runtimes[worker_id].queue.append(
        Task(
            task_id="queued-worker-task",
            source_id=worker_id,
            created_epoch=2,
            created_time_s=2.0,
            priority="low",
            size_bytes=32,
        )
    )

    runner.clock.now_s = 3.0
    runner.stage_runtimes[worker_id].buffers[sink_id].enqueue(
        data={"task": object(), "target": sink_id},
        priority=BufferPriority.LOW,
        size_bytes=16,
        ttl=10.0,
    )

    runner.clock.now_s = 5.0
    state = runner._build_controller_state(epoch=5, time_s=5.0)

    assert state.queue_depths["BulkPath"] == 1
    assert state.oldest_age_s["BulkPath"] == pytest.approx(3.0)
    assert state.ttl_pressure["BulkPath"] == pytest.approx(0.2)


def test_send_or_buffer_enforces_live_link_bandwidth_budget(tmp_path, monkeypatch) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_id = _stage_id(runner, "SpaceSource")
    target_id = next(iter(runner.stage_runtimes[source_id].buffers))

    def resolve_link(src_stage: str, dst_stage: str):
        if (src_stage, dst_stage) == (source_id, target_id):
            return True, 100.0 / 125_000.0, 0.0
        return False, 0.0, 0.0

    monkeypatch.setattr(runner, "_resolve_link", resolve_link)

    runner._send_or_buffer(
        source_id,
        target_id,
        Task(
            task_id="task-1",
            source_id=source_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=80,
        ),
        epoch=0,
    )
    runner._send_or_buffer(
        source_id,
        target_id,
        Task(
            task_id="task-2",
            source_id=source_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=80,
        ),
        epoch=0,
    )

    assert len(runner.inflight) == 1
    assert runner.stage_runtimes[source_id].buffers[target_id].size == 1


def test_flush_and_live_traffic_share_same_link_bandwidth_budget(tmp_path, monkeypatch) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_id = _stage_id(runner, "SpaceSource")
    target_id = next(iter(runner.stage_runtimes[source_id].buffers))
    buffer = runner.stage_runtimes[source_id].buffers[target_id]

    def resolve_link(src_stage: str, dst_stage: str):
        if (src_stage, dst_stage) == (source_id, target_id):
            return True, 100.0 / 125_000.0, 0.0
        return False, 0.0, 0.0

    monkeypatch.setattr(runner, "_resolve_link", resolve_link)

    buffer.enqueue(
        data={
            "task": Task(
                task_id="buffered-task",
                source_id=source_id,
                created_epoch=0,
                created_time_s=0.0,
                priority="low",
                size_bytes=80,
            ),
            "target": target_id,
        },
        priority=BufferPriority.LOW,
        size_bytes=80,
        ttl=10.0,
    )

    runner._flush_buffers(epoch=0)
    runner._send_or_buffer(
        source_id,
        target_id,
        Task(
            task_id="live-task",
            source_id=source_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=80,
        ),
        epoch=0,
    )

    assert len(runner.inflight) == 1
    assert buffer.size == 1


def test_distinct_service_pairs_share_budget_on_common_bottleneck_hop(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_a = _stage_id(runner, "SpaceSource")
    source_b = _stage_id(runner, "IoTSource")
    target_id = _stage_id(runner, "Fusion")
    node_a, node_b, target_node = _node_ids(runner, 3)

    runner.placement[source_a] = node_a
    runner.placement[source_b] = node_b
    runner.placement[target_id] = target_node

    def path_hops(src_node: str, dst_node: str):
        assert dst_node == target_node
        if src_node == node_a:
            return [
                (node_a, "shared", {"availability": 1.0, "bandwidth": 1.0, "latency": 0.0}),
                (
                    "shared",
                    target_node,
                    {
                        "availability": 1.0,
                        "bandwidth": 100.0 / 125_000.0,
                        "latency": 0.0,
                    },
                ),
            ]
        if src_node == node_b:
            return [
                (node_b, "shared", {"availability": 1.0, "bandwidth": 1.0, "latency": 0.0}),
                (
                    "shared",
                    target_node,
                    {
                        "availability": 1.0,
                        "bandwidth": 100.0 / 125_000.0,
                        "latency": 0.0,
                    },
                ),
            ]
        raise AssertionError((src_node, dst_node))

    monkeypatch.setattr(runner, "_path_hops", path_hops)

    runner._send_or_buffer(
        source_a,
        target_id,
        Task(
            task_id="task-a",
            source_id=source_a,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=80,
        ),
        epoch=0,
    )
    runner._send_or_buffer(
        source_b,
        target_id,
        Task(
            task_id="task-b",
            source_id=source_b,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=80,
        ),
        epoch=0,
    )

    total_buffered = (
        runner.stage_runtimes[source_a].buffers[target_id].size
        + runner.stage_runtimes[source_b].buffers[target_id].size
    )

    assert len(runner.inflight) == 1
    assert total_buffered == 1


def test_path_resolution_requires_eclypse_infrastructure_path(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_id = _stage_id(runner, "IoTSource")
    target_id = _stage_id(runner, "Fusion")
    source_node = _node_id_for_layer(runner, "iot")
    target_node = _node_id_for_layer(runner, "edge")
    runner.placement[source_id] = source_node
    runner.placement[target_id] = target_node

    monkeypatch.setattr(runner.infrastructure, "path", lambda _src, _dst: None)

    assert runner._path_hops(source_node, target_node) == []
    assert runner._resolve_link(source_id, target_id) == (False, 0.0, 0.0)

    monkeypatch.setattr(runner.infrastructure, "path", None)

    assert runner._path_hops(source_node, target_node) == []


def test_resolve_link_uses_eclypse_path_resources_for_aggregate_link_assets(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_id = _stage_id(runner, "IoTSource")
    target_id = _stage_id(runner, "Fusion")
    source_node = _node_id_for_layer(runner, "iot")
    target_node = _node_id_for_layer(runner, "edge")
    runner.placement[source_id] = source_node
    runner.placement[target_id] = target_node

    monkeypatch.setattr(
        runner,
        "_path_hops",
        lambda _src, _dst: [
            ("a", "b", {"availability": 1.0, "bandwidth": 999.0, "latency": 999.0})
        ],
    )
    monkeypatch.setattr(
        runner.infrastructure,
        "path_resources",
        lambda _src, _dst: {"availability": 1.0, "bandwidth": 12.5, "latency": 34.0},
    )

    assert runner._resolve_link(source_id, target_id) == (True, 12.5, 34.0)


def test_send_or_buffer_uses_eclypse_route_cost_for_delivery_delay(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_id = _stage_id(runner, "IoTSource")
    target_id = _stage_id(runner, "Fusion")
    source_node = _node_id_for_layer(runner, "iot")
    target_node = _node_id_for_layer(runner, "edge")
    runner.placement[source_id] = source_node
    runner.placement[target_id] = target_node

    route_hops = [
        (
            source_node,
            target_node,
            {"availability": 1.0, "bandwidth": 1.0, "latency": 250.0},
        )
    ]
    monkeypatch.setattr(
        runner.infrastructure,
        "path",
        lambda _src, _dst: (route_hops, 0.0),
    )
    monkeypatch.setattr(
        runner.infrastructure,
        "path_resources",
        lambda _src, _dst: {"availability": 1.0, "bandwidth": 1.0, "latency": 250.0},
    )
    monkeypatch.setattr(runner, "_bandwidth_to_bytes", lambda _bw_mbps: 10**18)

    task = Task(
        task_id="route-cost-delay",
        source_id=source_id,
        created_epoch=0,
        created_time_s=0.0,
        priority="low",
        size_bytes=250_000,
    )
    runner._send_or_buffer(source_id, target_id, task, epoch=0)

    assert len(runner.inflight) == 1
    assert runner.inflight[0].deliver_epoch == 3


def test_contact_closure_blocks_runtime_transport_without_structural_path_mutation(tmp_path) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_id = _stage_id(runner, "IoTSource")
    target_id = _stage_id(runner, "Fusion")
    source_node = _node_id_for_layer(runner, "iot")
    target_node = _node_id_for_layer(runner, "edge")
    runner.placement[source_id] = source_node
    runner.placement[target_id] = target_node

    edge_attrs = runner.infrastructure.edges[source_node, target_node]
    edge_attrs["bandwidth"] = 100.0
    edge_attrs["latency"] = 10.0
    edge_attrs["contact_open"] = 0.0
    edge_attrs["contact_bandwidth"] = 0.0
    edge_attrs["contact_latency"] = 10.0
    runner._invalidate_infrastructure_views()

    assert runner.infrastructure.path(source_node, target_node) is not None
    assert runner._resolve_link(source_id, target_id) == (False, 0.0, 0.0)


def test_contact_overlay_supplies_route_cost_bandwidth_without_mutating_structural_link(
    tmp_path,
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_id = _stage_id(runner, "IoTSource")
    target_id = _stage_id(runner, "Fusion")
    source_node = _node_id_for_layer(runner, "iot")
    target_node = _node_id_for_layer(runner, "edge")
    runner.placement[source_id] = source_node
    runner.placement[target_id] = target_node

    edge_attrs = runner.infrastructure.edges[source_node, target_node]
    edge_attrs["bandwidth"] = 0.0
    edge_attrs["latency"] = 999.0
    edge_attrs["contact_open"] = 1.0
    edge_attrs["contact_bandwidth"] = 1.0
    edge_attrs["contact_latency"] = 250.0
    runner._invalidate_infrastructure_views()

    assert runner._resolve_link(source_id, target_id) == (True, 1.0, 250.0)

    task = Task(
        task_id="contact-route-cost",
        source_id=source_id,
        created_epoch=0,
        created_time_s=0.0,
        priority="low",
        size_bytes=250_000,
    )
    assert runner._message_delay_epochs(source_id, target_id, task, fallback_latency_ms=250.0) == 3


def test_failure_records_are_node_only(tmp_path, monkeypatch) -> None:
    config = _runner_config(initial_workers=1, max_workers=1)
    config["reliability"] = {
        "failures": {
            "mode": "deterministic",
            "events": [{"epoch": 0, "node_down": ["Cloud_0"]}],
        }
    }
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="separated_loops",
        seed=0,
        out_root=tmp_path,
    )
    _freeze_existing_placement(runner, monkeypatch)

    record = runner._run_epoch(0)

    assert set(record["failures"]) == {"node_down", "failed_tasks"}


def test_runner_uses_stock_eclypse_infrastructure_for_contact_runtime(tmp_path) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)

    assert type(runner.infrastructure).__name__ == "Infrastructure"
    assert not hasattr(runner.infrastructure, "invalidate_views")
    runner._invalidate_infrastructure_views()


def test_run_epoch_reports_deadline_misses_and_per_priority_completions(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")

    runner.stage_runtimes[sink_id].profile["deadline_s"] = 2.0
    runner.stage_runtimes[sink_id].profile["deadline"] = 2.0
    runner.stage_runtimes[sink_id].queue.extend(
        [
            Task(
                task_id="high-miss",
                source_id=sink_id,
                created_epoch=0,
                created_time_s=0.0,
                priority="high",
                size_bytes=32,
            ),
            Task(
                task_id="low-hit",
                source_id=sink_id,
                created_epoch=3,
                created_time_s=3.5,
                priority="low",
                size_bytes=32,
            ),
        ]
    )

    _freeze_existing_placement(runner, monkeypatch, epoch=3)

    record = runner._run_epoch(3)

    assert record["completions"]["count"] == 2
    assert record["completions"]["deadline_miss_count"] == 1
    assert record["completions"]["slo_violation_count"] == 1
    assert record["completions"]["per_priority"] == {
        "high": {
            "completed": 1,
            "deadline_miss_count": 1,
            "slo_violation_count": 1,
        },
        "low": {
            "completed": 1,
            "deadline_miss_count": 0,
            "slo_violation_count": 0,
        },
    }


def test_run_epoch_reports_cross_tier_traffic_bytes(tmp_path, monkeypatch) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    space_source_id = _stage_id(runner, "SpaceSource")
    iot_source_id = _stage_id(runner, "IoTSource")
    fusion_id = _stage_id(runner, "Fusion")

    runner.placement[space_source_id] = _node_id_for_layer(runner, "space")
    runner.placement[iot_source_id] = _node_id_for_layer(runner, "iot")
    runner.placement[fusion_id] = _node_id_for_layer(runner, "edge")
    _relax_all_node_capacities(runner)

    runner.workload_streams[space_source_id] = _StaticStream(1)
    runner.workload_streams[iot_source_id] = _StaticStream(1)
    _freeze_existing_placement(runner, monkeypatch)

    record = runner._run_epoch(0)

    assert record["traffic"]["cross_tier_bytes"] == 128
    assert record["traffic"]["by_tier_pair"] == {
        "iot->edge": 64,
        "space->edge": 64,
    }


def test_run_epoch_reports_resource_energy_and_reliability_signals(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")
    edge_node = _node_id_for_layer(runner, "edge")
    runner.infrastructure.nodes[edge_node].update(
        {
            "cpu": 64.0,
            "ram": 64.0,
            "storage": 1024.0,
            "idle_power_w": 2.0,
            "dynamic_power_w_per_cpu": 3.0,
            "battery_capacity_j": 100.0,
            "battery_remaining_j": 100.0,
        }
    )
    runner.placement[sink_id] = edge_node
    runner.stage_runtimes[sink_id].profile["processing_time_s"] = 1.0
    runner.stage_runtimes[sink_id].profile["resources"]["cpu"] = 2.0
    runner.application.nodes[sink_id]["cpu"] = 2.0
    runner.stage_runtimes[sink_id].queue.append(
        Task(
            task_id="resource-accounted",
            source_id=sink_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )

    _freeze_existing_placement(runner, monkeypatch)
    runner.infrastructure.nodes[edge_node].update({"cpu": 4.0, "ram": 64.0, "storage": 1024.0})

    record = runner._run_epoch(0)

    assert record["resources"]["cpu"]["demand"] == pytest.approx(2.0)
    assert record["resources"]["cpu"]["capacity"] == pytest.approx(4.0)
    assert record["resources"]["cpu"]["utilization"] == pytest.approx(0.5)
    assert record["resources"]["nodes"][edge_node]["cpu"]["utilization"] == pytest.approx(0.5)
    assert record["resources"]["reserved"]["nodes"][edge_node]["stages"][sink_id][
        "resource_demand"
    ]["cpu"] == pytest.approx(2.0)
    assert record["resources"]["active_runtime"]["nodes"][edge_node]["cpu"]["demand"] == pytest.approx(2.0)
    assert record["energy"]["total_j"] == pytest.approx(8.0)
    assert record["energy"]["by_node"][edge_node]["battery_remaining_j"] == pytest.approx(92.0)
    assert record["reliability"]["generated_tasks"] == 0
    assert record["reliability"]["completed_tasks"] == 1
    assert record["reliability"]["failed_tasks"] == 0


def test_runner_rejects_placement_that_violates_eclypse_node_resources(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")
    edge_node = _node_id_for_layer(runner, "edge")
    _freeze_existing_placement(runner, monkeypatch)
    runner.infrastructure.nodes[edge_node].update({"cpu": 1.0, "ram": 1.0, "storage": 1.0})
    runner.application.nodes[sink_id].update({"cpu": 4.0, "ram": 4.0, "storage": 4.0})

    monkeypatch.setattr(
        runner.controller,
        "step",
        lambda _state: ControllerAction(placement={sink_id: edge_node}),
    )

    with pytest.raises(ValueError, match="violates Eclypse substrate resources"):
        runner._run_epoch(0)


def test_runner_rejects_static_mapping_that_violates_eclypse_anchor_assets(
    tmp_path,
) -> None:
    config = _runner_config(initial_workers=0, max_workers=0)
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="static",
        seed=0,
        out_root=tmp_path,
    )
    source_id = _stage_id(runner, "IoTSource")
    cloud_node = _node_id_for_layer(runner, "cloud")

    placement_controller = runner._build_controller("placement_only:best_fit", runner.config)
    action = placement_controller.step(
        runner._build_controller_state(
            epoch=0,
            time_s=0.0,
        )
    )
    assert action.placement
    runner.placement.update(action.placement)
    runner.placement[source_id] = cloud_node

    with pytest.raises(ValueError, match="violates Eclypse substrate resources"):
        runner._validate_placement_for_active_services(force=True)


def test_resource_snapshot_uses_eclypse_placement_view_demands(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")
    edge_node = _node_id_for_layer(runner, "edge")
    _freeze_existing_placement(runner, monkeypatch)
    runner.infrastructure.nodes[edge_node].update({"cpu": 64.0, "ram": 64.0, "storage": 1024.0})
    runner.application.nodes[sink_id].update({"cpu": 3.0, "ram": 5.0, "storage": 7.0})
    runner.stage_runtimes[sink_id].profile["resources"].update(
        {"cpu": 1.0, "ram": 1.0, "storage": 1.0}
    )
    runner.stage_runtimes[sink_id].queue.append(
        Task(
            task_id="placement-view-demand",
            source_id=sink_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )
    monkeypatch.setattr(
        runner.controller,
        "step",
        lambda _state: ControllerAction(placement={sink_id: edge_node}),
    )

    record = runner._run_epoch(0)

    assert record["resources"]["nodes"][edge_node]["stages"][sink_id]["resource_demand"] == {
        "cpu": 3.0,
        "ram": 5.0,
        "storage": 7.0,
    }
    assert record["resources"]["reserved"]["nodes"][edge_node]["stages"][sink_id]["resource_demand"] == {
        "cpu": 3.0,
        "ram": 5.0,
        "storage": 7.0,
    }
    assert record["resources"]["active_runtime"]["nodes"][edge_node]["stages"][sink_id]["resource_demand"] == {
        "cpu": 3.0,
        "ram": 5.0,
        "storage": 7.0,
    }


def test_resource_snapshot_splits_reserved_from_active_runtime_demands(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")
    edge_node = _node_id_for_layer(runner, "edge")
    _freeze_existing_placement(runner, monkeypatch)
    _relax_all_node_capacities(runner)
    runner.application.nodes[sink_id].update({"cpu": 3.0, "ram": 4.0, "storage": 5.0})
    runner.stage_runtimes[sink_id].profile["resources"].update(
        {"cpu": 1.0, "ram": 1.0, "storage": 1.0}
    )
    monkeypatch.setattr(
        runner.controller,
        "step",
        lambda _state: ControllerAction(placement={sink_id: edge_node}),
    )

    record = runner._run_epoch(0)

    reserved_stage = record["resources"]["reserved"]["nodes"][edge_node]["stages"][sink_id]
    assert reserved_stage["resource_demand"] == {
        "cpu": 3.0,
        "ram": 4.0,
        "storage": 5.0,
    }
    assert record["resources"]["reserved"]["cpu"]["demand"] >= 3.0
    assert record["resources"]["reserved"]["ram"]["demand"] >= 4.0
    assert record["resources"]["reserved"]["storage"]["demand"] >= 5.0
    assert record["resources"]["active_runtime"]["cpu"]["demand"] == pytest.approx(0.0)
    assert record["resources"]["active_runtime"]["nodes"] == {}
    assert record["resources"]["cpu"]["demand"] == pytest.approx(0.0)


def test_resource_snapshot_reports_ram_and_storage_overcommit_without_cpu_overcommit(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")
    edge_node = _node_id_for_layer(runner, "edge")
    runner.infrastructure.nodes[edge_node].update({"cpu": 64.0, "ram": 64.0, "storage": 1024.0})
    runner.placement[sink_id] = edge_node
    runner.stage_runtimes[sink_id].profile["processing_time_s"] = 10.0
    runner.stage_runtimes[sink_id].profile["resources"].update(
        {"cpu": 2.0, "ram": 6.0, "storage": 10.0}
    )
    runner.application.nodes[sink_id].update({"cpu": 2.0, "ram": 6.0, "storage": 10.0})
    runner.stage_runtimes[sink_id].queue.append(
        Task(
            task_id="ram-storage-overcommit",
            source_id=sink_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )

    _freeze_existing_placement(runner, monkeypatch)
    runner.infrastructure.nodes[edge_node].update({"cpu": 64.0, "ram": 2.0, "storage": 4.0})

    record = runner._run_epoch(0)

    assert record["resources"]["cpu"]["overcommit"] == pytest.approx(0.0)
    assert record["resources"]["ram"]["demand"] == pytest.approx(6.0)
    assert record["resources"]["ram"]["capacity"] == pytest.approx(2.0)
    assert record["resources"]["ram"]["overcommit"] == pytest.approx(4.0)
    assert record["resources"]["storage"]["overcommit"] == pytest.approx(6.0)
    assert record["resources"]["nodes"][edge_node]["stages"][sink_id]["resource_demand"] == {
        "cpu": 2.0,
        "ram": 6.0,
        "storage": 10.0,
    }


def test_ram_and_storage_capacity_throttle_processing_credit_without_cpu_overcommit(
    tmp_path, monkeypatch
) -> None:
    constrained_runner = _build_runner(tmp_path / "constrained", initial_workers=1, max_workers=1)
    unconstrained_runner = _build_runner(tmp_path / "unconstrained", initial_workers=1, max_workers=1)
    constrained_sink_id = _stage_id(constrained_runner, "Sink")
    unconstrained_sink_id = _stage_id(unconstrained_runner, "Sink")
    constrained_node = _node_id_for_layer(constrained_runner, "edge")
    unconstrained_node = _node_id_for_layer(unconstrained_runner, "edge")

    constrained_runner.infrastructure.nodes[constrained_node].update(
        {"cpu": 64.0, "ram": 64.0, "storage": 1024.0}
    )
    unconstrained_runner.infrastructure.nodes[unconstrained_node].update(
        {"cpu": 64.0, "ram": 64.0, "storage": 1024.0}
    )
    constrained_runner.placement[constrained_sink_id] = constrained_node
    unconstrained_runner.placement[unconstrained_sink_id] = unconstrained_node

    for runner, sink_id in [
        (constrained_runner, constrained_sink_id),
        (unconstrained_runner, unconstrained_sink_id),
    ]:
        runtime = runner.stage_runtimes[sink_id]
        runtime.profile["processing_time_s"] = 1.0
        runtime.profile["resources"].update({"cpu": 2.0, "ram": 6.0, "storage": 10.0})
        runner.application.nodes[sink_id].update({"cpu": 2.0, "ram": 6.0, "storage": 10.0})
        runtime.queue.extend(
            Task(
                task_id=f"{sink_id}-task-{idx}",
                source_id=sink_id,
                created_epoch=0,
                created_time_s=0.0,
                priority="low",
                size_bytes=32,
            )
            for idx in range(2)
        )
        _freeze_existing_placement(runner, monkeypatch)

    constrained_runner.infrastructure.nodes[constrained_node].update(
        {"cpu": 64.0, "ram": 2.0, "storage": 4.0}
    )

    constrained_record_0 = constrained_runner._run_epoch(0)
    constrained_record_1 = constrained_runner._run_epoch(1)
    constrained_record_2 = constrained_runner._run_epoch(2)
    unconstrained_record_0 = unconstrained_runner._run_epoch(0)

    assert unconstrained_record_0["completions"]["count"] == 1
    assert constrained_record_0["completions"]["count"] == 0
    assert constrained_record_1["completions"]["count"] == 0
    assert constrained_record_2["completions"]["count"] == 1
    assert constrained_record_0["resources"]["cpu"]["overcommit"] == pytest.approx(0.0)
    assert constrained_record_0["resources"]["ram"]["overcommit"] > 0.0
    assert constrained_record_0["resources"]["storage"]["overcommit"] > 0.0
    assert constrained_record_0["resources"]["nodes"][constrained_node]["stages"][constrained_sink_id][
        "processing_factor"
    ] == pytest.approx(2.0 / 6.0)


def test_energy_charges_idle_nodes_and_network_transport(tmp_path, monkeypatch) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    source_id = _stage_id(runner, "IoTSource")
    fusion_id = _stage_id(runner, "Fusion")
    iot_node = _node_id_for_layer(runner, "iot")
    edge_node = _node_id_for_layer(runner, "edge")
    idle_cloud = _node_id_for_layer(runner, "cloud")
    runner.infrastructure.nodes[iot_node]["idle_power_w"] = 1.0
    runner.infrastructure.nodes[edge_node].update(
        {
            "cpu": 8.0,
            "ram": 16.0,
            "storage": 128.0,
            "idle_power_w": 2.0,
            "dynamic_power_w_per_cpu": 3.0,
            "dynamic_power_w_per_ram": 0.5,
            "dynamic_power_w_per_storage": 0.25,
        }
    )
    runner.infrastructure.nodes[idle_cloud]["idle_power_w"] = 5.0
    runner.placement[source_id] = iot_node
    runner.placement[fusion_id] = edge_node
    runner.stage_runtimes[fusion_id].profile["processing_time_s"] = 10.0
    runner.stage_runtimes[fusion_id].profile["resources"].update(
        {"cpu": 2.0, "ram": 4.0, "storage": 8.0}
    )
    runner.stage_runtimes[fusion_id].queue.append(
        Task(
            task_id="energy-accounted",
            source_id=fusion_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )
    if runner.infrastructure.has_edge(iot_node, edge_node):
        runner.infrastructure.edges[iot_node, edge_node]["energy_j_per_byte"] = 0.01

    runner._record_traffic(source_id, fusion_id, 100)
    resource_snapshot = runner._compute_resource_snapshot()
    energy = runner._compute_energy_snapshot(resource_snapshot)

    assert energy["compute_total_j"] == pytest.approx(18.0)
    assert energy["network_total_j"] == pytest.approx(1.0)
    assert energy["total_j"] == pytest.approx(19.0)
    assert energy["by_node"][idle_cloud]["idle_energy_j"] == pytest.approx(5.0)
    assert energy["by_node"][edge_node]["dynamic_energy_j"] == pytest.approx(10.0)
    assert energy["by_link_or_tier_pair"][f"{iot_node}->{edge_node}"]["energy_j"] == pytest.approx(1.0)


def test_energy_charges_idle_power_for_failed_provisioned_nodes(
    tmp_path, monkeypatch
) -> None:
    config = _runner_config(initial_workers=1, max_workers=1)
    config["reliability"] = {
        "failures": {
            "mode": "deterministic",
            "events": [{"epoch": 0, "node_down": ["Edge_0"]}],
        }
    }
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="separated_loops",
        seed=0,
        out_root=tmp_path,
    )
    runner.infrastructure.nodes["Edge_0"]["idle_power_w"] = 7.0
    _freeze_existing_placement(runner, monkeypatch)

    record = runner._run_epoch(0)

    assert "Edge_0" not in runner.infrastructure.available.nodes
    assert record["energy"]["by_node"]["Edge_0"]["idle_energy_j"] == pytest.approx(7.0)


def test_calibrated_v2x_energy_profile_contributes_to_run_energy(tmp_path) -> None:
    config = _runner_config(initial_workers=0, max_workers=1)
    config["calibration"] = {"energy_profile": "v2x_edge_iot_energy_v1"}
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="no_scaling",
        seed=0,
        out_root=tmp_path / "energy_profile",
    )

    record = runner._run_epoch(0)

    assert record["energy"]["by_node"]["IoT_0"]["idle_energy_j"] == pytest.approx(1.8)
    assert record["energy"]["by_node"]["Edge_0"]["idle_energy_j"] == pytest.approx(45.0)
    assert record["energy"]["by_node"]["Cloud_0"]["idle_energy_j"] == pytest.approx(180.0)
    assert record["energy"]["compute_total_j"] > 0.0


def test_node_cpu_capacity_throttles_processing_credit(tmp_path, monkeypatch) -> None:
    weak_runner = _build_runner(tmp_path / "weak", initial_workers=1, max_workers=1)
    strong_runner = _build_runner(tmp_path / "strong", initial_workers=1, max_workers=1)
    weak_sink_id = _stage_id(weak_runner, "Sink")
    strong_sink_id = _stage_id(strong_runner, "Sink")
    weak_node = _node_id_for_layer(weak_runner, "edge")
    strong_node = _node_id_for_layer(strong_runner, "edge")

    weak_runner.infrastructure.nodes[weak_node]["cpu"] = 0.5
    strong_runner.infrastructure.nodes[strong_node]["cpu"] = 4.0
    weak_runner.placement[weak_sink_id] = weak_node
    strong_runner.placement[strong_sink_id] = strong_node

    for runner, sink_id in [(weak_runner, weak_sink_id), (strong_runner, strong_sink_id)]:
        runner.infrastructure.nodes[_node_id_for_layer(runner, "edge")].update(
            {"cpu": 64.0, "ram": 64.0, "storage": 1024.0}
        )
        runner.stage_runtimes[sink_id].profile["processing_time_s"] = 1.0
        runner.stage_runtimes[sink_id].profile["resources"]["cpu"] = 2.0
        runner.application.nodes[sink_id]["cpu"] = 2.0
        runner.stage_runtimes[sink_id].queue.extend(
            Task(
                task_id=f"{sink_id}-task-{idx}",
                source_id=sink_id,
                created_epoch=0,
                created_time_s=0.0,
                priority="low",
                size_bytes=32,
            )
            for idx in range(2)
        )
        _freeze_existing_placement(runner, monkeypatch)

    weak_runner.infrastructure.nodes[weak_node]["cpu"] = 0.5
    strong_runner.infrastructure.nodes[strong_node]["cpu"] = 4.0

    weak_record_0 = weak_runner._run_epoch(0)
    weak_record_1 = weak_runner._run_epoch(1)
    strong_record_0 = strong_runner._run_epoch(0)

    assert weak_record_0["completions"]["count"] == 0
    assert weak_record_1["completions"]["count"] == 0
    assert strong_record_0["completions"]["count"] == 1
    assert weak_record_0["resources"]["cpu"]["overcommit"] > 0.0
    assert strong_record_0["resources"]["cpu"]["overcommit"] == pytest.approx(0.0)


def test_deterministic_node_failure_drops_inflight_task_and_updates_reliability(
    tmp_path, monkeypatch
) -> None:
    config = _runner_config(initial_workers=1, max_workers=1)
    config["reliability"] = {
        "failures": {
            "mode": "deterministic",
            "events": [{"epoch": 0, "node_down": ["Edge_0"]}],
        }
    }
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="separated_loops",
        seed=0,
        out_root=tmp_path,
    )
    sink_id = _stage_id(runner, "Sink")
    runner.placement[sink_id] = "Edge_0"
    runner.inflight.append(
        InFlightMessage(
            deliver_epoch=0,
            target_stage=sink_id,
            task=Task(
                task_id="lost-to-node-failure",
                source_id=sink_id,
                created_epoch=0,
                created_time_s=0.0,
                priority="low",
                size_bytes=32,
            ),
        )
    )

    _freeze_existing_placement(runner, monkeypatch)

    record = runner._run_epoch(0)

    assert record["failures"]["node_down"] == ["Edge_0"]
    assert record["failures"]["failed_tasks"] == 1
    assert record["reliability"]["failure_event_failed_tasks"] == 1
    assert record["reliability"]["buffer_failed_tasks"] == 0
    assert runner.infrastructure.nodes["Edge_0"]["availability"] == pytest.approx(0.0)
    assert "Edge_0" not in runner.infrastructure.available.nodes
    assert runner.stage_runtimes[sink_id].queue == deque()

    runner._run_epoch(1)

    assert runner.infrastructure.nodes["Edge_0"]["availability"] > 0.0
    assert "Edge_0" in runner.infrastructure.available.nodes


def test_seeded_bernoulli_failures_are_reproducible(tmp_path, monkeypatch) -> None:
    config = _runner_config(initial_workers=1, max_workers=1)
    config["reliability"] = {
        "failures": {
            "mode": "bernoulli",
            "node_failure_probability": 0.4,
        }
    }
    first = ContinuumBenchRunner(config=config, controller_name="separated_loops", seed=11, out_root=tmp_path / "a")
    second = ContinuumBenchRunner(config=config, controller_name="separated_loops", seed=11, out_root=tmp_path / "b")
    _freeze_existing_placement(first, monkeypatch)
    _freeze_existing_placement(second, monkeypatch)

    first_records = [first._run_epoch(epoch)["failures"] for epoch in range(3)]
    second_records = [second._run_epoch(epoch)["failures"] for epoch in range(3)]

    assert first_records == second_records
    assert any(record["node_down"] for record in first_records)


def test_inflight_message_survives_transient_target_failure_before_delivery(
    tmp_path, monkeypatch
) -> None:
    # CORE-1 regression: a message still in transit (deliver_epoch in the future) must NOT be
    # dropped or counted as failed merely because its target node is transiently down in an
    # intervening epoch. A message is lost only if its target is unavailable AT delivery time.
    config = _runner_config(initial_workers=1, max_workers=1)
    config["reliability"] = {
        "failures": {
            "mode": "deterministic",
            "events": [{"epoch": 1, "node_down": ["Edge_0"]}],
        }
    }
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="separated_loops",
        seed=0,
        out_root=tmp_path,
    )
    sink_id = _stage_id(runner, "Sink")
    runner.placement[sink_id] = "Edge_0"
    runner.inflight.append(
        InFlightMessage(
            deliver_epoch=2,  # delivers at epoch 2, AFTER the transient epoch-1 outage
            target_stage=sink_id,
            task=Task(
                task_id="survives-transient-outage",
                source_id=sink_id,
                created_epoch=0,
                created_time_s=0.0,
                priority="low",
                size_bytes=32,
            ),
        )
    )
    _freeze_existing_placement(runner, monkeypatch)

    runner._run_epoch(0)  # node up; message not yet due
    assert len(runner.inflight) == 1

    record1 = runner._run_epoch(1)  # Edge_0 transiently DOWN; message still in transit
    assert record1["failures"]["node_down"] == ["Edge_0"]
    assert record1["failures"]["failed_tasks"] == 0
    assert len(runner.inflight) == 1  # not dropped: target is down but delivery is not due

    record2 = runner._run_epoch(2)  # Edge_0 restored; message now delivers
    assert record2["failures"]["failed_tasks"] == 0
    assert all(m.task.task_id != "survives-transient-outage" for m in runner.inflight)


def test_flush_rebuffer_does_not_double_count_buffer_stats(tmp_path, monkeypatch) -> None:
    # CORE-2 regression: when a buffered item is dequeued for a delivery attempt but its link is
    # unavailable and it is re-buffered, it must NOT be counted as transmitted (it was not sent)
    # nor counted as buffered a second time. drop_rate/expire_rate are normalized by items_buffered,
    # so double-counting the re-buffer path biases the reported reliability rates.
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)

    stage_id, target_id = next(
        (s, t) for s, rt in runner.stage_runtimes.items() for t in rt.buffers
    )
    buffer = runner.stage_runtimes[stage_id].buffers[target_id]
    task = Task(
        task_id="rebuffered-item",
        source_id=stage_id,
        created_epoch=0,
        created_time_s=0.0,
        priority="low",
        size_bytes=10,
    )
    assert buffer.enqueue(
        data={"task": task, "target": target_id},
        priority=BufferPriority.LOW,
        size_bytes=10,
        ttl=3600.0,
    )
    assert buffer.stats["items_buffered"] == 1
    assert buffer.stats["items_transmitted"] == 0

    # Force the re-buffer branch: the buffer gate link is available (so the item is dequeued for a
    # delivery attempt), but the per-item delivery link is unavailable (so it is re-buffered, not
    # sent). The processed buffer is iterated first, so its gate is the first _resolve_link call.
    state = {"armed": False}
    my_key = (stage_id, target_id)

    def fake_resolve(s_id, t_id):
        if state["armed"]:
            return (False, 0.0, 0.0)
        if (s_id, t_id) == my_key:
            state["armed"] = True
        return (True, 100.0, 1.0)

    monkeypatch.setattr(runner, "_resolve_link", fake_resolve)

    runner._flush_buffers(epoch=0)

    assert buffer.size == 1  # item was re-buffered, not delivered
    assert buffer.stats["items_buffered"] == 1  # re-buffering must not re-count as newly buffered
    assert buffer.stats["items_transmitted"] == 0  # not actually transmitted


def test_autoscaler_fixed_placement_freezes_existing_on_scale(tmp_path, monkeypatch) -> None:
    # E2 ablation contract: with replan_on_scale=False and periodic_replan=False, a scale-up places
    # ONLY the newly-activated worker (incremental) and never migrates an existing worker; with no new
    # worker there is no replan at all. The default (coupled) does a full global re-solve.
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=2)
    _freeze_existing_placement(runner, monkeypatch)
    state = runner._build_controller_state(epoch=5, time_s=5.0)
    assert state.placement  # already placed at epoch 0
    pool = next(iter(state.worker_pools))
    scale = ScaleAction(stage=pool, active_workers=state.worker_pools[pool].active_workers + 1)

    cfg = dict(runner.config)
    cfg["placement"] = {**runner.config.get("placement", {}), "replan_on_scale": False, "periodic_replan": False}
    fixed = runner._build_controller("hpa", cfg)

    # no scale event + periodic disabled at a non-tick epoch -> the fixed arm never replans
    none_placement, none_replanned, _, _ = fixed._maybe_replan(state, [])
    assert none_replanned is False
    assert none_placement is None

    # a scale-up activates a new worker that still must be placed (incrementally); existing frozen
    projected = fixed._project_active_services(state, [scale])
    new_workers = [s for s in projected if s not in state.placement]
    assert new_workers
    placement_fixed, replanned_fixed, _, _ = fixed._maybe_replan(state, [scale])
    assert replanned_fixed is True
    assert placement_fixed is not None
    assert all(w in placement_fixed for w in new_workers)  # new worker placed
    for svc, node in state.placement.items():
        if svc in placement_fixed:
            assert placement_fixed[svc] == node  # no migration of existing workers

    # the default (coupled) controller does a full re-solve and also returns a complete placement
    coupled = runner._build_controller("hpa", runner.config)
    placement_coupled, replanned_coupled, _, _ = coupled._maybe_replan(state, [scale])
    assert replanned_coupled is True
    assert placement_coupled is not None


def test_autoscaler_milp_replan_strategy_plans_and_guards_capacity(tmp_path, monkeypatch) -> None:
    # An autoscaler can use the global MILP re-optimizer as its placement strategy (a genuinely
    # migrating planner, unlike the order-stable heuristics), and the capacity guard rejects an
    # over-subscribed mapping so the controller defers gracefully instead of the runner hard-failing.
    from continuum_bench.controllers.placement_views import mapping_fits_node_capacity

    runner = _build_runner(tmp_path, initial_workers=1, max_workers=2)
    _freeze_existing_placement(runner, monkeypatch)
    state = runner._build_controller_state(epoch=5, time_s=5.0)
    cfg = dict(runner.config)
    cfg["placement"] = {**runner.config.get("placement", {}), "strategy": "milp"}
    ctrl = runner._build_controller("hpa", cfg)

    active = ctrl._project_active_services(state, [])
    mapping = ctrl._plan_placement(state, active)
    assert mapping  # the MILP planner produced a placement through the autoscaler path
    assert all(s in mapping for s in active if s in state.application.nodes)
    assert mapping_fits_node_capacity(state, mapping) is True

    # piling every service onto the smallest node must be rejected by the capacity guard
    nodes = {str(n): a for n, a in state.infrastructure.nodes(data=True)}
    smallest = min(nodes, key=lambda n: float(nodes[n].get("cpu", 1e9)))
    assert mapping_fits_node_capacity(state, {s: smallest for s in mapping}) is False


def test_learned_stub_controller_logs_reward_without_external_ml_dependency(tmp_path) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    learned = runner._build_controller("learned_stub", runner.config)

    action = learned.step(runner._build_controller_state(epoch=0, time_s=0.0))
    assert action.metadata["controller"] == "learned_stub"
    assert action.metadata["policy"] == "linear_feature_policy"
    assert action.metadata["heuristic_delegate"] is False

    runner = ContinuumBenchRunner(
        config=_runner_config(initial_workers=1, max_workers=1),
        controller_name="learned_stub",
        seed=0,
        out_root=tmp_path / "learned",
    )
    record = runner._run_epoch(0)

    assert "controller_reward" in record
    assert record["controller_reward"]["reward"] <= 0.0
    assert record["controller_reward"]["components"].keys() >= {
        "latency",
        "slo",
        "energy",
        "reliability",
        "churn",
    }


def test_learned_stub_controller_uses_configured_policy_weights(tmp_path) -> None:
    config = _runner_config(initial_workers=1, max_workers=2)
    config["learned_controller"] = {
        "queue_weight": 2.0,
        "age_weight": 0.0,
        "ttl_weight": 0.0,
        "scale_up_score": 4.0,
        "scale_down_score": -1.0,
        "scale_step": 1,
        "cooldown_epochs": 0,
    }
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="learned_stub",
        seed=0,
        out_root=tmp_path / "learned_config",
    )
    bulk_worker = runner.scenario.worker_pools["BulkPath"][0]
    runner.stage_runtimes[bulk_worker].queue.append(
        Task(
            task_id="learned-policy-pressure",
            source_id=bulk_worker,
            created_epoch=0,
            created_time_s=0.0,
            priority="normal",
            size_bytes=1,
        )
    )
    runner.stage_runtimes[bulk_worker].queue.append(
        Task(
            task_id="learned-policy-pressure-2",
            source_id=bulk_worker,
            created_epoch=0,
            created_time_s=0.0,
            priority="normal",
            size_bytes=1,
        )
    )

    action = runner.controller.step(runner._build_controller_state(epoch=0, time_s=0.0))

    assert action.scale == [ScaleAction(stage="BulkPath", active_workers=2)]
    assert action.metadata["policy_features"]["BulkPath"]["score"] == pytest.approx(4.0)


def test_rl_meta_controller_records_meta_action_in_reward_trajectory(tmp_path) -> None:
    config = _runner_config(initial_workers=1, max_workers=2)
    config["rl_meta_controller"] = {
        "policy": "scripted",
        "action_sequence": [
            {
                "scale_direction": "up",
                "placement_strategy": "best_fit",
                "replan": True,
            }
        ],
        "cooldown_epochs": 0,
    }
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="rl_meta",
        seed=0,
        out_root=tmp_path / "rl_meta",
    )

    record = runner._run_epoch(0)

    assert record["controller_reward"]["reward"] <= 0.0
    assert runner._last_decision_trajectory_record is not None
    action = runner._last_decision_trajectory_record["action"]
    assert action["metadata"]["controller"] == "rl_meta"
    assert action["metadata"]["meta_action"] == {
        "scale_direction": "up",
        "replan": True,
        "placement_strategy": "best_fit",
    }
    assert action["metadata"]["rl_ready"] is True


def test_runner_drain_epochs_continue_runtime_without_generating_new_work(
    tmp_path,
) -> None:
    config = _runner_config(initial_workers=1, max_workers=1)
    config["epochs"] = 2
    config["drain_epochs"] = 3
    runner = ContinuumBenchRunner(
        config=config,
        controller_name="separated_loops",
        seed=0,
        out_root=tmp_path,
    )
    stream = _ConstantStream(1)
    runner.workload_streams = {
        source_stage: stream for source_stage in runner.scenario.source_stages
    }

    result = runner.run()

    assert result["summary"]["metadata"]["workload_epochs"] == 2
    assert result["summary"]["metadata"]["drain_epochs"] == 3
    assert result["summary"]["metadata"]["epochs"] == 5
    assert result["summary"]["totals"]["epochs"] == 5
    assert sum(record["completions"]["generated"] for record in runner.epoch_records[:2]) > 0
    assert sum(record["completions"]["generated"] for record in runner.epoch_records[2:]) == 0
    assert set(stream.epochs) == {0, 1}


def test_runner_applies_global_workload_repeat_to_piecewise_streams(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mmpp_module, "_sample_poisson", lambda rate, rng: int(rate))
    config = _runner_config(initial_workers=1, max_workers=1)
    config["workload"]["repeat"] = True
    config["workload"]["streams"] = {
        source_stage: {
            "generator": "piecewise",
            "segments": [
                {"epochs": 1, "rate": 1.0},
                {"epochs": 1, "rate": 4.0},
            ],
        }
        for source_stage in ("SpaceSource", "IoTSource")
    }

    runner = ContinuumBenchRunner(
        config=config,
        controller_name="separated_loops",
        seed=0,
        out_root=tmp_path,
    )

    space_source_id = _stage_id(runner, "SpaceSource")
    assert [runner.workload_streams[space_source_id].sample(epoch) for epoch in range(4)] == [
        1,
        4,
        1,
        4,
    ]


@pytest.mark.parametrize("controller_name", ["hpa", "keda", "rl_meta"])
def test_autoscaling_baseline_controllers_run_epoch(tmp_path, controller_name: str) -> None:
    config = _runner_config(initial_workers=1, max_workers=2)
    runner = ContinuumBenchRunner(
        config=config,
        controller_name=controller_name,
        seed=0,
        out_root=tmp_path / controller_name,
    )

    record = runner._run_epoch(0)

    assert record["controller_reward"]["reward"] <= 0.0
    assert record["placement"]


def test_final_epoch_counts_overdue_unfinished_sink_task_as_slo_violation(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")

    runner.stage_runtimes[sink_id].profile["deadline_s"] = 2.0
    runner.stage_runtimes[sink_id].profile["deadline"] = 2.0
    runner.stage_runtimes[sink_id].profile["processing_time_s"] = 10.0
    runner.stage_runtimes[sink_id].queue.append(
        Task(
            task_id="late-unfinished",
            source_id=sink_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )

    _freeze_existing_placement(runner, monkeypatch, epoch=runner.epochs - 1)

    record = runner._run_epoch(runner.epochs - 1)

    assert record["completions"]["count"] == 0
    assert record["completions"]["deadline_miss_count"] == 0
    assert record["completions"]["slo_violation_count"] == 1
    assert record["completions"]["per_priority"] == {
        "low": {
            "completed": 0,
            "deadline_miss_count": 0,
            "slo_violation_count": 1,
        }
    }


def test_final_epoch_counts_overdue_unfinished_inflight_task_as_slo_violation(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")

    runner.stage_runtimes[sink_id].profile["deadline_s"] = 2.0
    runner.stage_runtimes[sink_id].profile["deadline"] = 2.0
    runner.inflight.append(
        InFlightMessage(
            deliver_epoch=runner.epochs + 1,
            target_stage=sink_id,
            task=Task(
                task_id="late-inflight",
                source_id=sink_id,
                created_epoch=0,
                created_time_s=0.0,
                priority="low",
                size_bytes=32,
            ),
        )
    )

    _freeze_existing_placement(runner, monkeypatch, epoch=runner.epochs - 1)

    record = runner._run_epoch(runner.epochs - 1)

    assert record["completions"]["count"] == 0
    assert record["completions"]["deadline_miss_count"] == 0
    assert record["completions"]["slo_violation_count"] == 1
    assert record["completions"]["slo_eligible_count"] == 1
    assert record["completions"]["slo_eligible_by_priority"] == {"low": 1}
    assert record["completions"]["per_priority"] == {
        "low": {
            "completed": 0,
            "deadline_miss_count": 0,
            "slo_violation_count": 1,
        }
    }


def test_summary_uses_runner_emitted_horizon_denominator_for_completed_and_unfinished_tasks(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")
    fusion_id = _stage_id(runner, "Fusion")

    runner.stage_runtimes[sink_id].profile["deadline_s"] = 2.0
    runner.stage_runtimes[sink_id].profile["deadline"] = 2.0
    runner.stage_runtimes[sink_id].queue.append(
        Task(
            task_id="eligible-completed",
            source_id=sink_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="high",
            size_bytes=32,
        )
    )

    _freeze_existing_placement(runner, monkeypatch, epoch=1)

    early_record = runner._run_epoch(1)

    runner.stage_runtimes[fusion_id].profile["processing_time_s"] = 10.0
    runner.stage_runtimes[fusion_id].queue.append(
        Task(
            task_id="eligible-unfinished",
            source_id=fusion_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )

    final_record = runner._run_epoch(runner.epochs - 1)
    summary = summarize_epochs(
        {
            "scenario": runner.scenario.name,
            "step_seconds": runner.step_seconds,
        },
        [early_record, final_record],
    )

    assert final_record["completions"]["slo_eligible_count"] == 2
    assert final_record["completions"]["slo_eligible_by_priority"] == {
        "high": 1,
        "low": 1,
    }
    assert summary["qos"]["deadline_miss_count"] == 0
    assert summary["qos"]["slo_violation_count"] == 1
    assert summary["qos"]["slo_eligible_count"] == 2
    assert summary["qos"]["slo_violation_rate"] == pytest.approx(0.5)
    assert summary["qos"]["per_priority"] == {
        "high": {
            "generated": 0,
            "completed": 1,
            "deadline_miss_count": 0,
            "slo_violation_count": 0,
            "slo_eligible_count": 1,
        },
        "low": {
            "generated": 0,
            "completed": 0,
            "deadline_miss_count": 0,
            "slo_violation_count": 1,
            "slo_eligible_count": 1,
        },
    }


def test_summary_counts_early_on_time_completion_in_horizon_slo_denominator(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")

    runner.stage_runtimes[sink_id].profile["deadline_s"] = 6.0
    runner.stage_runtimes[sink_id].profile["deadline"] = 6.0
    runner.stage_runtimes[sink_id].queue.append(
        Task(
            task_id="early-on-time",
            source_id=sink_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )

    _freeze_existing_placement(runner, monkeypatch, epoch=1)

    early_record = runner._run_epoch(1)
    final_record = runner._run_epoch(runner.epochs - 1)
    summary = summarize_epochs(
        {
            "scenario": runner.scenario.name,
            "step_seconds": runner.step_seconds,
        },
        [early_record, final_record],
    )

    assert early_record["completions"]["count"] == 1
    assert early_record["completions"]["deadline_miss_count"] == 0
    assert final_record["completions"]["slo_eligible_count"] == 1
    assert final_record["completions"]["slo_eligible_by_priority"] == {"low": 1}
    assert summary["qos"]["slo_eligible_count"] == 1
    assert summary["qos"]["slo_violation_count"] == 0
    assert summary["qos"]["slo_violation_rate"] == 0.0


def test_summary_excludes_completed_task_whose_deadline_matures_after_run_horizon(
    tmp_path, monkeypatch
) -> None:
    runner = _build_runner(tmp_path, initial_workers=1, max_workers=1)
    sink_id = _stage_id(runner, "Sink")

    runner.stage_runtimes[sink_id].profile["deadline_s"] = 20.0
    runner.stage_runtimes[sink_id].profile["deadline"] = 20.0
    runner.stage_runtimes[sink_id].queue.append(
        Task(
            task_id="late-maturing-deadline",
            source_id=sink_id,
            created_epoch=0,
            created_time_s=0.0,
            priority="low",
            size_bytes=32,
        )
    )

    _freeze_existing_placement(runner, monkeypatch, epoch=1)

    early_record = runner._run_epoch(1)
    final_record = runner._run_epoch(runner.epochs - 1)
    summary = summarize_epochs(
        {
            "scenario": runner.scenario.name,
            "step_seconds": runner.step_seconds,
        },
        [early_record, final_record],
    )

    assert early_record["completions"]["count"] == 1
    assert early_record["completions"]["deadline_miss_count"] == 0
    assert final_record["completions"]["slo_eligible_count"] == 0
    assert final_record["completions"]["slo_eligible_by_priority"] == {}
    assert summary["qos"]["slo_eligible_count"] == 0
    assert summary["qos"]["slo_violation_count"] == 0
    assert summary["qos"]["slo_violation_rate"] == 0.0
