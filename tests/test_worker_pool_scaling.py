from __future__ import annotations

from dataclasses import fields

from continuum_bench._eclypse import ensure_eclypse_on_path
from continuum_bench.controllers import (
    ControllerState,
    HPAController,
    KEDAController,
    LearnedStubController,
    PlacementOnlyController,
    RLMetaController,
    ScaleAction,
    SeparatedLoopsController,
    WorkerPoolManager,
    WorkerPoolSnapshot,
)
from continuum_bench.controllers.placement_views import plan_placement
from continuum_bench.controllers import separated_loops
from continuum_bench.controllers import autoscaling_baselines
from continuum_bench.controllers import learned_stub
from continuum_bench.controllers import rl_meta
from continuum_bench.controllers import placement_only
from continuum_bench.runtime.skeleton import SkeletonRole, SkeletonService

ensure_eclypse_on_path()
from eclypse.graph import Application, Infrastructure
from eclypse.graph.assets import Symbolic
from eclypse.graph.assets.defaults import (
    bandwidth,
    cpu,
    get_default_path_aggregators,
    latency,
    ram,
    storage,
)


def _application(nodes: dict[str, dict], edges: list[tuple[str, str]]) -> Application:
    app = Application(
        application_id="test_app",
        include_default_assets=False,
        node_assets={
            "placement_layers": Symbolic(
                lower_bound=[],
                upper_bound=["space", "iot", "edge", "cloud"],
                init_fn_or_value=[],
            ),
        },
    )
    for service_id, attrs in nodes.items():
        node_attrs = dict(attrs)
        if service_id.startswith("worker"):
            service = SkeletonService(
                service_id,
                SkeletonRole.TRANSFORM,
                profile=dict(node_attrs),
            )
            app.add_service(service, strict=False, **node_attrs)
        else:
            app.add_node(service_id, strict=False, **node_attrs)
    for src, dst in edges:
        app.add_edge(src, dst, strict=False)
    return app


def _infrastructure(
    nodes: list[str] | dict[str, dict],
    latencies: dict[tuple[str, str], float] | None = None,
) -> Infrastructure:
    infra = Infrastructure(
        infrastructure_id="test_infra",
        include_default_assets=False,
        node_assets={
            "placement_layers": Symbolic(
                lower_bound=[],
                upper_bound=["space", "iot", "edge", "cloud"],
                init_fn_or_value=[],
            ),
        },
        edge_assets={
            "latency": latency(init_fn_or_value=0.0),
            "bandwidth": bandwidth(init_fn_or_value=0.0),
        },
        path_assets_aggregators=get_default_path_aggregators(),
        seed=0,
    )
    node_attrs = dict(nodes) if isinstance(nodes, dict) else {node_id: {} for node_id in nodes}
    for node_id, attrs in node_attrs.items():
        infra.add_node(node_id, strict=False, availability=1.0, **dict(attrs))
    for (src, dst), latency_ms in (latencies or {}).items():
        infra.add_edge(
            src,
            dst,
            strict=False,
            latency=float(latency_ms),
            bandwidth=1.0,
        )
    return infra


class RecordingStrategy:
    def __init__(self, node_id: str = "node-a") -> None:
        self.node_id = node_id
        self.calls: list[list[str]] = []

    def place(self, infrastructure, application, placements, placement_view) -> dict[str, str]:
        del infrastructure, placements, placement_view
        service_ids = list(application.nodes)
        self.calls.append(service_ids)
        return {service_id: self.node_id for service_id in service_ids}


def _worker_pool_snapshot(
    active_worker_ids: list[str],
    inactive_worker_ids: list[str],
    *,
    stage: str = "BulkPath",
    min_workers: int = 0,
    ready_worker_ids: list[str] | None = None,
    starting_worker_ids: list[str] | None = None,
) -> WorkerPoolSnapshot:
    ready_ids = list(active_worker_ids if ready_worker_ids is None else ready_worker_ids)
    starting_ids = list(
        []
        if starting_worker_ids is None
        else starting_worker_ids
    )
    kwargs = {
        "stage": stage,
        "min_workers": min_workers,
        "max_workers": len(active_worker_ids) + len(inactive_worker_ids),
        "active_workers": len(active_worker_ids),
        "total_workers": len(active_worker_ids) + len(inactive_worker_ids),
        "active_worker_ids": active_worker_ids,
        "inactive_worker_ids": inactive_worker_ids,
    }
    field_names = {field.name for field in fields(WorkerPoolSnapshot)}
    if "ready_workers" in field_names:
        kwargs["ready_workers"] = len(ready_ids)
    if "ready_worker_ids" in field_names:
        kwargs["ready_worker_ids"] = ready_ids
    if "starting_workers" in field_names:
        kwargs["starting_workers"] = len(starting_ids)
    if "starting_worker_ids" in field_names:
        kwargs["starting_worker_ids"] = starting_ids

    return WorkerPoolSnapshot(
        stage=stage,
        min_workers=min_workers,
        max_workers=len(active_worker_ids) + len(inactive_worker_ids),
        active_workers=len(active_worker_ids),
        total_workers=len(active_worker_ids) + len(inactive_worker_ids),
        active_worker_ids=active_worker_ids,
        inactive_worker_ids=inactive_worker_ids,
        **{
            key: value
            for key, value in kwargs.items()
            if key
            not in {
                "stage",
                "min_workers",
                "max_workers",
                "active_workers",
                "total_workers",
                "active_worker_ids",
                "inactive_worker_ids",
            }
        },
    )


def _controller_state(
    *,
    application: Application | None = None,
    infrastructure: Infrastructure | None = None,
    placement: dict[str, str] | None = None,
    queue_depths: dict[str, int] | None = None,
    oldest_age_s: dict[str, float] | None = None,
    ttl_pressure: dict[str, float] | None = None,
    worker_pools: dict[str, WorkerPoolSnapshot] | None = None,
    metadata: dict | None = None,
    epoch: int = 1,
) -> ControllerState:
    app = application or _application(
        {
            "source": {"cpu": 1.0, "active": True},
            "worker-0": {"cpu": 2.0, "active": True},
            "worker-1": {"cpu": 2.0, "active": True},
            "sink": {"cpu": 1.0, "active": True},
        },
        [("source", "worker-0"), ("worker-0", "sink"), ("worker-1", "sink")],
    )
    infra = infrastructure or _infrastructure(["node-a", "node-b"])
    pools = worker_pools or {
        "BulkPath": _worker_pool_snapshot(["worker-0"], ["worker-1"])
    }

    return ControllerState(
        epoch=epoch,
        time_s=float(epoch),
        seed=0,
        application=app,
        infrastructure=infra,
        placement=placement or {},
        queue_depths=queue_depths or {},
        oldest_age_s=oldest_age_s or {},
        ttl_pressure=ttl_pressure or {},
        buffer_items={},
        buffer_bytes={},
        worker_pools=pools,
        metadata=metadata or {},
    )


def _tiered_controller_state(
    *,
    placement: dict[str, str] | None = None,
) -> ControllerState:
    return _controller_state(
        application=_application(
            {
                "source": {
                    "cpu": 1.0,
                    "active": True,
                    "placement_layers": ["iot"],
                },
                "worker-0": {"cpu": 2.0, "active": True},
                "worker-1": {"cpu": 2.0, "active": True},
                "sink": {
                    "cpu": 1.0,
                    "active": True,
                    "placement_layers": ["cloud"],
                },
            },
            [("source", "worker-0"), ("worker-0", "sink"), ("worker-1", "sink")],
        ),
        infrastructure=_infrastructure(
            {
                "iot-a": {"placement_layers": ["iot"]},
                "edge-a": {"placement_layers": ["edge"]},
                "cloud-a": {"placement_layers": ["cloud"]},
            },
            latencies={
                ("iot-a", "edge-a"): 2.0,
                ("edge-a", "iot-a"): 2.0,
                ("edge-a", "cloud-a"): 2.0,
                ("cloud-a", "edge-a"): 2.0,
                ("iot-a", "cloud-a"): 20.0,
                ("cloud-a", "iot-a"): 20.0,
            },
        ),
        placement=placement,
    )


def _cost_sensitive_controller_state() -> ControllerState:
    return _controller_state(
        application=_application(
            {
                "source": {
                    "cpu": 1.0,
                    "active": True,
                    "placement_layers": ["iot"],
                    "output_size_bytes": 10_000_000,
                },
                "worker-0": {"cpu": 8.0, "active": True, "processing_time": 0.2},
                "worker-1": {"cpu": 8.0, "active": True, "processing_time": 0.2},
                "sink": {
                    "cpu": 1.0,
                    "active": True,
                    "placement_layers": ["cloud"],
                },
            },
            [("source", "worker-0"), ("worker-0", "sink"), ("worker-1", "sink")],
        ),
        infrastructure=_infrastructure(
            {
                "iot-a": {"placement_layers": ["iot"]},
                "edge-a": {"placement_layers": ["edge"], "cpu": 2.0, "processing_time": 2.0},
                "cloud-a": {"placement_layers": ["cloud"], "cpu": 16.0, "processing_time": 0.1},
            },
            latencies={
                ("iot-a", "edge-a"): 1.0,
                ("edge-a", "iot-a"): 1.0,
                ("iot-a", "cloud-a"): 2.3,
                ("cloud-a", "iot-a"): 2.3,
                ("edge-a", "cloud-a"): 2.0,
                ("cloud-a", "edge-a"): 2.0,
            },
        ),
    )


def _dependency_frontier_controller_state() -> ControllerState:
    return _controller_state(
        application=_application(
            {
                "source": {
                    "cpu": 1.0,
                    "active": True,
                    "placement_layers": ["iot"],
                    "output_size_bytes": 2_000_000,
                },
                "bridge-a": {"cpu": 2.0, "active": True, "processing_time": 0.2},
                "worker-0": {"cpu": 2.0, "active": True, "processing_time": 0.2},
                "worker-1": {"cpu": 2.0, "active": True, "processing_time": 0.2},
                "sink": {
                    "cpu": 1.0,
                    "active": True,
                    "placement_layers": ["cloud"],
                },
            },
            [
                ("source", "bridge-a"),
                ("bridge-a", "worker-0"),
                ("bridge-a", "worker-1"),
                ("worker-0", "sink"),
                ("worker-1", "sink"),
            ],
        ),
        infrastructure=_infrastructure(
            {
                "iot-a": {"placement_layers": ["iot"]},
                "edge-a": {"placement_layers": ["edge"]},
                "cloud-a": {"placement_layers": ["cloud"]},
            },
            latencies={
                ("iot-a", "edge-a"): 2.0,
                ("edge-a", "iot-a"): 2.0,
                ("edge-a", "cloud-a"): 2.0,
                ("cloud-a", "edge-a"): 2.0,
                ("iot-a", "cloud-a"): 20.0,
                ("cloud-a", "iot-a"): 20.0,
            },
        ),
    )


def _one_sided_anchor_chain_controller_state() -> ControllerState:
    return _controller_state(
        application=_application(
            {
                "source": {
                    "cpu": 1.0,
                    "active": True,
                    "placement_layers": ["iot"],
                    "output_size_bytes": 2_000_000,
                },
                "bridge-a": {"cpu": 2.0, "active": True, "processing_time": 0.2},
                "worker-0": {"cpu": 2.0, "active": True, "processing_time": 0.2},
                "worker-1": {"cpu": 2.0, "active": True, "processing_time": 0.2},
                "sink": {"cpu": 1.0, "active": True, "processing_time": 0.1},
            },
            [
                ("source", "bridge-a"),
                ("bridge-a", "worker-0"),
                ("worker-0", "sink"),
                ("worker-1", "sink"),
            ],
        ),
        infrastructure=_infrastructure(
            {
                "iot-a": {"placement_layers": ["iot"]},
                "edge-a": {"placement_layers": ["edge"]},
                "cloud-a": {"placement_layers": ["cloud"]},
            },
            latencies={
                ("iot-a", "edge-a"): 2.0,
                ("edge-a", "iot-a"): 2.0,
                ("edge-a", "cloud-a"): 2.0,
                ("cloud-a", "edge-a"): 2.0,
                ("iot-a", "cloud-a"): 20.0,
                ("cloud-a", "iot-a"): 20.0,
            },
        ),
    )


def test_worker_pool_manager_set_active_workers_updates_activation_and_resources() -> None:
    application = _application(
        {
            "source": {"cpu": 1.0, "active": True},
            "worker-0": {"cpu": 2.0, "active": True},
            "worker-1": {"cpu": 2.0, "active": True},
            "worker-2": {"cpu": 2.0, "active": True},
        },
        [],
    )

    manager = WorkerPoolManager(
        application=application,
        worker_pools={"BulkPath": ["worker-0", "worker-1", "worker-2"]},
        min_workers={"BulkPath": 0},
        initial_workers={"BulkPath": 0},
    )

    active_workers = manager.set_active_workers("BulkPath", 2)

    assert active_workers == ["worker-0", "worker-1"]
    assert application.services["worker-2"].is_active is False
    assert application.nodes["worker-2"]["active"] is False
    assert application.nodes["worker-2"]["cpu"] <= 1e-8
    assert manager.snapshots()["BulkPath"].inactive_worker_ids == ["worker-2"]


def test_worker_pool_manager_keeps_new_workers_in_starting_state_until_startup_delay_elapses() -> None:
    clock = {"now_s": 0.0}
    application = _application(
        {
            "source": {"cpu": 1.0, "active": True},
            "worker-0": {"cpu": 2.0, "active": True},
            "worker-1": {"cpu": 2.0, "active": True},
        },
        [],
    )

    manager = WorkerPoolManager(
        application=application,
        worker_pools={"BulkPath": ["worker-0", "worker-1"]},
        min_workers={"BulkPath": 0},
        initial_workers={"BulkPath": 0},
        startup_delay_s={"BulkPath": 2.0},
        time_fn=lambda: clock["now_s"],
    )

    manager.set_active_workers("BulkPath", 1)

    snapshot = manager.snapshots()["BulkPath"]
    assert snapshot.active_worker_ids == ["worker-0"]
    assert snapshot.ready_worker_ids == []
    assert snapshot.starting_worker_ids == ["worker-0"]

    clock["now_s"] = 2.0

    snapshot = manager.snapshots()["BulkPath"]
    assert snapshot.ready_worker_ids == ["worker-0"]
    assert snapshot.starting_worker_ids == []


def test_worker_pool_manager_initial_workers_are_ready_immediately_with_startup_delay() -> None:
    clock = {"now_s": 0.0}
    application = _application(
        {
            "source": {"cpu": 1.0, "active": True},
            "worker-0": {"cpu": 2.0, "active": True},
            "worker-1": {"cpu": 2.0, "active": True},
        },
        [],
    )

    manager = WorkerPoolManager(
        application=application,
        worker_pools={"BulkPath": ["worker-0", "worker-1"]},
        min_workers={"BulkPath": 0},
        initial_workers={"BulkPath": 1},
        startup_delay_s={"BulkPath": 2.0},
        time_fn=lambda: clock["now_s"],
    )

    snapshot = manager.snapshots()["BulkPath"]

    assert snapshot.active_worker_ids == ["worker-0"]
    assert snapshot.ready_worker_ids == ["worker-0"]
    assert snapshot.starting_worker_ids == []


def test_separated_loops_scales_from_declared_controller_state_fields() -> None:
    controller = SeparatedLoopsController(
        placement_strategy="min_latency",
        queue_up_threshold=10,
        queue_down_threshold=-1,
        age_up_threshold_s=100.0,
        age_down_threshold_s=-1.0,
        ttl_pressure_up_threshold=1.0,
        ttl_pressure_down_threshold=-1.0,
        cooldown_epochs=0,
        place_every_epochs=99,
    )

    state = _controller_state(
        placement={"source": "node-a", "worker-0": "node-a", "sink": "node-b"},
        queue_depths={"BulkPath": 12},
        oldest_age_s={"BulkPath": 0.0},
        ttl_pressure={"BulkPath": 0.0},
    )

    action = controller.step(state)

    assert action.scale == [ScaleAction(stage="BulkPath", active_workers=2)]


def test_hpa_controller_scales_by_utilization_ratio_and_tolerance() -> None:
    controller = HPAController(
        target_utilization=0.5,
        tolerance=0.05,
        placement_strategy="best_fit",
        replan_every_epochs=99,
        cooldown_epochs=0,
    )
    high_utilization = _controller_state(
        placement={"source": "node-a", "worker-0": "node-a", "sink": "node-b"},
        queue_depths={"BulkPath": 10},
        worker_pools={
            "BulkPath": _worker_pool_snapshot(
                ["worker-0", "worker-1"],
                ["worker-2", "worker-3"],
            )
        },
    )

    high_action = controller.step(high_utilization)

    assert high_action.scale == [ScaleAction(stage="BulkPath", active_workers=4)]
    assert high_action.metadata["controller"] == "hpa"
    assert high_action.metadata["pool_metrics"]["BulkPath"]["desired_workers"] == 4

    stable_action = controller.step(
        _controller_state(
            placement={"source": "node-a", "worker-0": "node-a", "sink": "node-b"},
            queue_depths={"BulkPath": 1},
            worker_pools={
                "BulkPath": _worker_pool_snapshot(
                    ["worker-0", "worker-1"],
                    ["worker-2", "worker-3"],
                )
            },
            epoch=2,
        )
    )

    assert stable_action.scale == []


def test_hpa_controller_recovers_from_zero_active_workers_when_work_is_queued() -> None:
    controller = HPAController(
        target_utilization=0.5,
        tolerance=0.05,
        placement_strategy="best_fit",
        replan_every_epochs=99,
        cooldown_epochs=0,
    )
    cold_state = _controller_state(
        placement={"source": "node-a", "sink": "node-b"},
        queue_depths={"BulkPath": 6},
        worker_pools={
            "BulkPath": _worker_pool_snapshot(
                [],
                ["worker-0", "worker-1"],
            )
        },
    )

    action = controller.step(cold_state)

    assert action.scale == [ScaleAction(stage="BulkPath", active_workers=1)]
    assert action.metadata["pool_metrics"]["BulkPath"]["desired_workers"] == 1


def test_keda_controller_uses_queue_trigger_and_scales_to_zero() -> None:
    controller = KEDAController(
        queue_target_per_worker=3,
        activation_queue_length=1,
        min_replicas=0,
        placement_strategy="best_fit",
        replan_every_epochs=99,
        cooldown_epochs=0,
    )
    active_state = _controller_state(
        placement={"source": "node-a", "sink": "node-b"},
        queue_depths={"BulkPath": 7},
        worker_pools={
            "BulkPath": _worker_pool_snapshot([], ["worker-0", "worker-1", "worker-2"])
        },
    )

    active_action = controller.step(active_state)

    assert active_action.scale == [ScaleAction(stage="BulkPath", active_workers=3)]
    assert active_action.placement
    assert active_action.metadata["controller"] == "keda"
    assert active_action.metadata["trigger_metrics"]["BulkPath"]["trigger_active"] is True

    idle_action = controller.step(
        _controller_state(
            placement={"source": "node-a", "worker-0": "node-a", "sink": "node-b"},
            queue_depths={"BulkPath": 0},
            worker_pools={
                "BulkPath": _worker_pool_snapshot(["worker-0"], ["worker-1", "worker-2"])
            },
            epoch=2,
        )
    )

    assert idle_action.scale == [ScaleAction(stage="BulkPath", active_workers=0)]


def test_separated_loops_replans_with_snapshot_active_workers_only() -> None:
    controller = SeparatedLoopsController(
        placement_strategy="min_latency",
        queue_up_threshold=100,
        queue_down_threshold=-1,
        age_up_threshold_s=100.0,
        age_down_threshold_s=-1.0,
        ttl_pressure_up_threshold=1.0,
        ttl_pressure_down_threshold=-1.0,
        cooldown_epochs=0,
        place_every_epochs=1,
    )
    strategy = RecordingStrategy()
    controller._strategy = strategy

    state = _controller_state()

    action = controller.step(state)

    assert set(action.placement) == {"source", "worker-0", "sink"}
    assert "worker-1" not in action.placement
    assert strategy.calls == [["source", "worker-0", "sink"]]


def test_separated_loops_keeps_current_placement_when_periodic_replan_is_temporarily_infeasible(
    monkeypatch,
) -> None:
    controller = SeparatedLoopsController(
        placement_strategy="min_latency",
        queue_up_threshold=100,
        queue_down_threshold=-1,
        age_up_threshold_s=100.0,
        age_down_threshold_s=-1.0,
        ttl_pressure_up_threshold=1.0,
        ttl_pressure_down_threshold=-1.0,
        cooldown_epochs=0,
        place_every_epochs=1,
    )
    calls = []

    def unavailable_plan(*args, **kwargs):
        del args, kwargs
        calls.append("replan")
        return {}

    monkeypatch.setattr(separated_loops, "plan_placement", unavailable_plan)
    state = _controller_state(
        placement={"source": "node-a", "worker-0": "node-a", "sink": "node-b"},
        epoch=2,
    )

    action = controller.step(state)

    assert calls == ["replan"]
    assert action.scale == []
    assert action.placement is None
    assert action.metadata["replan"] is False
    assert action.metadata["replan_deferred"] is True


def test_autoscaling_baselines_defer_scale_when_replan_after_scaling_is_infeasible(
    monkeypatch,
) -> None:
    controller = HPAController(
        placement_strategy="best_fit",
        replan_every_epochs=99,
        target_utilization=0.5,
        tolerance=0.0,
        cooldown_epochs=0,
    )

    def unavailable_plan(*args, **kwargs):
        del args, kwargs
        return {}

    monkeypatch.setattr(autoscaling_baselines, "plan_placement", unavailable_plan)
    state = _controller_state(
        placement={"source": "node-a", "worker-0": "node-a", "sink": "node-b"},
        queue_depths={"BulkPath": 10},
        worker_pools={
            "BulkPath": _worker_pool_snapshot(["worker-0"], ["worker-1", "worker-2"])
        },
        epoch=5,
    )

    action = controller.step(state)

    assert action.scale == []
    assert action.placement is None
    assert action.metadata["replan"] is False
    assert action.metadata["replan_deferred"] is True


def test_learned_stub_keeps_current_placement_when_periodic_replan_is_temporarily_infeasible(
    monkeypatch,
) -> None:
    controller = LearnedStubController(
        placement_strategy="best_fit",
        replan_every_epochs=1,
        scale_up_score=999.0,
        scale_down_score=-1.0,
    )

    def unavailable_plan(*args, **kwargs):
        del args, kwargs
        return {}

    monkeypatch.setattr(learned_stub, "plan_placement", unavailable_plan)
    state = _controller_state(
        placement={"source": "node-a", "worker-0": "node-a", "sink": "node-b"},
        epoch=3,
    )

    action = controller.step(state)

    assert action.scale == []
    assert action.placement is None
    assert action.metadata["replan"] is False
    assert action.metadata["replan_deferred"] is True


def test_placement_only_excludes_inactive_workers_from_snapshot_projection() -> None:
    controller = PlacementOnlyController(strategy="round_robin")
    strategy = RecordingStrategy()
    controller._strategy = strategy

    state = _controller_state()

    action = controller.step(state)

    assert set(action.placement) == {"source", "worker-0", "sink"}
    assert "worker-1" not in action.placement
    assert strategy.calls == [["source", "worker-0", "sink"]]


def test_placement_only_keeps_last_placement_when_periodic_replan_is_temporarily_infeasible(
    monkeypatch,
) -> None:
    controller = PlacementOnlyController(strategy="round_robin", replan_every_epochs=1)
    controller._last_placement = {"source": "node-a", "worker-0": "node-a", "sink": "node-b"}

    def unavailable_plan(*args, **kwargs):
        del args, kwargs
        return {}

    monkeypatch.setattr(placement_only, "plan_placement", unavailable_plan)
    state = _controller_state(
        placement={"source": "node-a", "worker-0": "node-a", "sink": "node-b"},
        epoch=2,
    )

    action = controller.step(state)

    assert action.placement is None
    assert action.metadata["replan"] is False
    assert action.metadata["replan_deferred"] is True


def test_eclypse_planner_uses_base_resources_for_projected_active_workers() -> None:
    node_assets = {
        "cpu": cpu(init_fn_or_value=0.0),
        "ram": ram(init_fn_or_value=0.0),
        "storage": storage(init_fn_or_value=0.0),
    }
    application = Application(
        application_id="resource_app",
        include_default_assets=False,
        node_assets=node_assets,
    )
    service = SkeletonService(
        "worker-0",
        SkeletonRole.TRANSFORM,
        profile={"cpu": 5.0, "ram": 5.0, "storage": 1.0, "active": True},
    )
    application.add_service(
        service,
        strict=False,
        cpu=5.0,
        ram=5.0,
        storage=1.0,
        active=True,
    )
    manager = WorkerPoolManager(
        application=application,
        worker_pools={"BulkPath": ["worker-0"]},
        min_workers={"BulkPath": 0},
        initial_workers={"BulkPath": 0},
    )
    infrastructure = Infrastructure(
        infrastructure_id="resource_infra",
        include_default_assets=False,
        node_assets=node_assets,
        seed=0,
    )
    infrastructure.add_node("tiny", strict=False, cpu=1.0, ram=1.0, storage=8.0)
    infrastructure.add_node("large", strict=False, cpu=8.0, ram=8.0, storage=8.0)
    state = _controller_state(
        application=application,
        infrastructure=infrastructure,
        worker_pools=manager.snapshots(),
        metadata={
            "service_base_attrs": {
                service_id: manager.base_attrs_for(service_id)
                for service_id in application.nodes
            }
        },
    )

    placement = plan_placement(state, RecordingStrategy(node_id="tiny"), ["worker-0"])

    assert placement == {}


def test_placement_only_static_strategy_is_case_insensitive() -> None:
    controller = PlacementOnlyController(
        strategy="STATIC",
        static_mapping={
            "source": "node-static",
            "worker-0": "node-static",
            "worker-1": "node-static",
            "sink": "node-static",
        },
    )
    controller._strategy = RecordingStrategy(node_id="node-dynamic")

    state = _controller_state()

    action = controller.step(state)

    assert action.placement == {
        "source": "node-static",
        "worker-0": "node-static",
        "sink": "node-static",
    }


def test_placement_only_all_local_targets_iot_nodes_without_breaking_anchors() -> None:
    controller = PlacementOnlyController(strategy="all_local")

    action = controller.step(_tiered_controller_state())

    assert action.scale == []
    assert action.placement == {
        "source": "iot-a",
        "worker-0": "iot-a",
        "sink": "cloud-a",
    }


def test_placement_only_all_edge_targets_edge_nodes_without_breaking_anchors() -> None:
    controller = PlacementOnlyController(strategy="all_edge")

    action = controller.step(_tiered_controller_state())

    assert action.scale == []
    assert action.placement == {
        "source": "iot-a",
        "worker-0": "edge-a",
        "sink": "cloud-a",
    }


def test_placement_only_all_cloud_targets_cloud_nodes_without_breaking_anchors() -> None:
    controller = PlacementOnlyController(strategy="all_cloud")

    action = controller.step(_tiered_controller_state())

    assert action.scale == []
    assert action.placement == {
        "source": "iot-a",
        "worker-0": "cloud-a",
        "sink": "cloud-a",
    }


def test_placement_only_nearest_feasible_edge_prefers_edge_nodes_without_breaking_anchors() -> None:
    controller = PlacementOnlyController(strategy="nearest_feasible_edge")

    action = controller.step(_tiered_controller_state())

    assert action.scale == []
    assert action.placement == {
        "source": "iot-a",
        "worker-0": "edge-a",
        "sink": "cloud-a",
    }


def test_separated_loops_cost_greedy_avoids_all_cloud_when_edge_cuts_transfer_cost() -> None:
    controller = SeparatedLoopsController(
        placement_strategy="cost_greedy",
        queue_up_threshold=100,
        queue_down_threshold=-1,
        age_up_threshold_s=100.0,
        age_down_threshold_s=-1.0,
        ttl_pressure_up_threshold=1.0,
        ttl_pressure_down_threshold=-1.0,
        cooldown_epochs=0,
        place_every_epochs=1,
    )

    action = controller.step(_cost_sensitive_controller_state())

    assert action.scale == []
    assert action.placement == {
        "source": "iot-a",
        "worker-0": "edge-a",
        "sink": "cloud-a",
    }


def test_separated_loops_dependency_aware_uses_edge_frontier_for_multi_stage_bridge() -> None:
    controller = SeparatedLoopsController(
        placement_strategy="dependency_aware",
        queue_up_threshold=100,
        queue_down_threshold=-1,
        age_up_threshold_s=100.0,
        age_down_threshold_s=-1.0,
        ttl_pressure_up_threshold=1.0,
        ttl_pressure_down_threshold=-1.0,
        cooldown_epochs=0,
        place_every_epochs=1,
    )

    action = controller.step(_dependency_frontier_controller_state())

    assert action.scale == []
    assert action.placement == {
        "source": "iot-a",
        "bridge-a": "edge-a",
        "worker-0": "edge-a",
        "sink": "cloud-a",
    }


def test_separated_loops_dependency_aware_keeps_one_sided_anchor_chain_on_edge_frontier() -> None:
    controller = SeparatedLoopsController(
        placement_strategy="dependency_aware",
        queue_up_threshold=100,
        queue_down_threshold=-1,
        age_up_threshold_s=100.0,
        age_down_threshold_s=-1.0,
        ttl_pressure_up_threshold=1.0,
        ttl_pressure_down_threshold=-1.0,
        cooldown_epochs=0,
        place_every_epochs=1,
    )

    action = controller.step(_one_sided_anchor_chain_controller_state())

    assert action.scale == []
    assert action.placement == {
        "source": "iot-a",
        "bridge-a": "edge-a",
        "worker-0": "edge-a",
        "sink": "edge-a",
    }


def test_placement_only_freezes_first_valid_plan_when_configured() -> None:
    controller = PlacementOnlyController(strategy="round_robin", freeze_after_initial=True)
    strategy = RecordingStrategy(node_id="node-a")
    controller._strategy = strategy

    first_action = controller.step(_controller_state(epoch=0))
    strategy.node_id = "node-b"

    second_action = controller.step(_controller_state(epoch=1))

    assert first_action.scale == []
    assert second_action.scale == []
    assert first_action.placement == {
        "source": "node-a",
        "worker-0": "node-a",
        "sink": "node-a",
    }
    assert second_action.placement is None
    assert second_action.metadata["replan"] is False
    assert strategy.calls == [["source", "worker-0", "sink"]]


def test_learned_stub_uses_internal_feature_policy_without_heuristic_delegate() -> None:
    controller = LearnedStubController(
        queue_weight=1.0,
        age_weight=0.0,
        ttl_weight=0.0,
        scale_up_score=3.0,
        scale_down_score=1.0,
        placement_strategy="best_fit",
    )
    state = _controller_state(
        queue_depths={"BulkPath": 5},
        oldest_age_s={"BulkPath": 0.0},
        ttl_pressure={"BulkPath": 0.0},
        worker_pools={"BulkPath": _worker_pool_snapshot(["worker-0"], ["worker-1"])},
    )

    action = controller.step(state)

    assert action.scale == [ScaleAction(stage="BulkPath", active_workers=2)]
    assert action.placement
    assert action.metadata["controller"] == "learned_stub"
    assert action.metadata["policy"] == "linear_feature_policy"
    assert action.metadata["policy_features"]["BulkPath"]["score"] == 5.0
    assert action.metadata["heuristic_delegate"] is False


def test_learned_stub_is_not_a_separated_loops_wrapper() -> None:
    controller = LearnedStubController()

    assert not hasattr(controller, "_delegate")


def test_rl_meta_threshold_policy_selects_scale_and_strategy_action() -> None:
    controller = RLMetaController(
        policy="threshold",
        placement_strategies=("best_fit", "min_latency"),
        queue_weight=1.0,
        age_weight=0.0,
        ttl_weight=0.0,
        scale_up_score=3.0,
        scale_down_score=1.0,
        cooldown_epochs=0,
    )
    state = _controller_state(
        queue_depths={"BulkPath": 5},
        oldest_age_s={"BulkPath": 0.0},
        ttl_pressure={"BulkPath": 0.0},
        worker_pools={"BulkPath": _worker_pool_snapshot(["worker-0"], ["worker-1"])},
    )

    action = controller.step(state)

    assert action.scale == [ScaleAction(stage="BulkPath", active_workers=2)]
    assert action.placement
    assert action.metadata["controller"] == "rl_meta"
    assert action.metadata["policy"] == "threshold"
    assert action.metadata["meta_action"] == {
        "scale_direction": "up",
        "replan": True,
        "placement_strategy": "min_latency",
    }
    assert action.metadata["action_space"]["placement_strategies"] == [
        "best_fit",
        "min_latency",
    ]
    assert action.metadata["rl_ready"] is True


def test_rl_meta_scripted_policy_uses_discrete_meta_actions() -> None:
    controller = RLMetaController(
        policy="scripted",
        action_sequence=[
            {
                "scale_direction": "down",
                "placement_strategy": "best_fit",
                "replan": True,
            },
            {
                "scale_direction": "hold",
                "placement_strategy": "best_fit",
                "replan": False,
            },
        ],
        cooldown_epochs=0,
    )
    state = _controller_state(
        worker_pools={"BulkPath": _worker_pool_snapshot(["worker-0"], ["worker-1"])},
        placement={
            "source": "node-a",
            "worker-0": "node-a",
            "sink": "node-a",
        },
    )

    first_action = controller.step(state)
    second_action = controller.step(_controller_state(worker_pools=state.worker_pools, placement=state.placement, epoch=2))

    assert first_action.scale == [ScaleAction(stage="BulkPath", active_workers=0)]
    assert first_action.placement
    assert first_action.metadata["meta_action"]["scale_direction"] == "down"
    assert second_action.scale == []
    assert second_action.placement is None
    assert second_action.metadata["meta_action"] == {
        "scale_direction": "hold",
        "replan": False,
        "placement_strategy": "best_fit",
    }


def test_rl_meta_defers_transient_replan_when_existing_placement_is_valid(monkeypatch) -> None:
    controller = RLMetaController(
        policy="scripted",
        action_sequence=[
            {
                "scale_direction": "hold",
                "placement_strategy": "best_fit",
                "replan": True,
            }
        ],
    )
    state = _controller_state(
        placement={
            "source": "node-a",
            "worker-0": "node-a",
            "sink": "node-a",
        }
    )

    def unavailable_plan(_state, _strategy, _active_services):
        return {}

    monkeypatch.setattr(rl_meta, "plan_placement", unavailable_plan)

    action = controller.step(state)

    assert action.scale == []
    assert action.placement is None
    assert action.metadata["replan"] is False
    assert action.metadata["replan_deferred"] is True
