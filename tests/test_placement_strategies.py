from continuum_bench._eclypse import ensure_eclypse_on_path
from continuum_bench.placement import BestFitStrategy, MinCTStrategy, RoundRobinStrategy
from continuum_bench.placement.eclypse_adapter import EclypsePlacementAdapter
from continuum_bench.placement.strategies import (
    CostGreedyStrategy,
    DependencyAwareStrategy,
    NearestFeasibleEdgeStrategy,
)
from continuum_bench.scenarios import build_scenario
from typing import Dict

import pytest

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
from eclypse.placement import PlacementView
from eclypse.placement.strategies import BestFitStrategy as EclypseBestFitStrategy
from eclypse.placement.strategies.round_robin import (
    RoundRobinStrategy as EclypseRoundRobinStrategy,
)


def _infrastructure(nodes, latencies=None) -> Infrastructure:
    infrastructure = Infrastructure(
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
        infrastructure.add_node(node_id, strict=False, availability=1.0, **dict(attrs))
    for (src, dst), latency_ms in (latencies or {}).items():
        infrastructure.add_edge(
            src,
            dst,
            strict=False,
            latency=float(latency_ms),
            bandwidth=1.0,
        )
    return infrastructure


def _application(nodes=None, edges=None) -> Application:
    application = Application(
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
    for service_id, attrs in (
        nodes
        or {
            "source_a": {
                "placement_layers": ["space"],
            }
        }
    ).items():
        application.add_node(service_id, strict=False, **dict(attrs))
    for src, dst in edges or []:
        application.add_edge(src, dst, strict=False)
    return application


def _service_id_with_suffix(application, suffix: str) -> str:
    return next(service_id for service_id in application.nodes if service_id.endswith(suffix))


def _placement_view(infrastructure: Infrastructure) -> PlacementView:
    return PlacementView(infrastructure)


def _resource_infrastructure(nodes: Dict[str, Dict[str, float]]) -> Infrastructure:
    infrastructure = Infrastructure(
        infrastructure_id="resource_infra",
        include_default_assets=False,
        node_assets={
            "cpu": cpu(init_fn_or_value=0.0),
            "ram": ram(init_fn_or_value=0.0),
            "storage": storage(init_fn_or_value=0.0),
        },
        edge_assets={
            "latency": latency(init_fn_or_value=0.0),
            "bandwidth": bandwidth(init_fn_or_value=0.0),
        },
        path_assets_aggregators=get_default_path_aggregators(),
        seed=0,
    )
    for node_id, attrs in nodes.items():
        infrastructure.add_node(node_id, strict=False, **dict(attrs))
    return infrastructure


def _resource_application(nodes: Dict[str, Dict[str, float]]) -> Application:
    application = Application(
        application_id="resource_app",
        include_default_assets=False,
        node_assets={
            "cpu": cpu(init_fn_or_value=0.0),
            "ram": ram(init_fn_or_value=0.0),
            "storage": storage(init_fn_or_value=0.0),
        },
    )
    for service_id, attrs in nodes.items():
        application.add_node(service_id, strict=False, **dict(attrs))
    return application


def _tiered_application() -> Application:
    return _application(
        {
            "source": {"placement_layers": ["iot"]},
            "worker": {"cpu": 2.0},
            "sink": {"placement_layers": ["cloud"]},
        },
        [("source", "worker"), ("worker", "sink")],
    )


def _cost_sensitive_application() -> Application:
    return _application(
        {
            "source": {"placement_layers": ["iot"], "output_size_bytes": 10_000_000},
            "worker": {"cpu": 8.0, "processing_time": 0.2},
            "sink": {"placement_layers": ["cloud"]},
        },
        [("source", "worker"), ("worker", "sink")],
    )


def _multi_stage_bridge_application() -> Application:
    return _application(
        {
            "source": {"placement_layers": ["iot"], "output_size_bytes": 2_000_000},
            "bridge_a": {"cpu": 2.0, "processing_time": 0.2},
            "bridge_b": {"cpu": 2.0, "processing_time": 0.2},
            "sink": {"placement_layers": ["cloud"]},
        },
        [
            ("source", "bridge_a"),
            ("bridge_a", "bridge_b"),
            ("bridge_b", "sink"),
        ],
    )


def _one_sided_anchor_chain_application() -> Application:
    return _application(
        {
            "source": {"placement_layers": ["iot"], "output_size_bytes": 2_000_000},
            "stage_a": {"cpu": 2.0, "processing_time": 0.2},
            "stage_b": {"cpu": 2.0, "processing_time": 0.2},
            "stage_c": {"cpu": 2.0, "processing_time": 0.2},
        },
        [
            ("source", "stage_a"),
            ("stage_a", "stage_b"),
            ("stage_b", "stage_c"),
        ],
    )


def test_round_robin_does_not_drop_anchor_constraints_when_no_node_matches() -> None:
    strategy = RoundRobinStrategy()
    infrastructure = _infrastructure(["Edge_1", "Edge_2"])
    application = _application()

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert mapping == {}


def test_round_robin_extends_eclypse_native_strategy() -> None:
    assert issubclass(RoundRobinStrategy, EclypseRoundRobinStrategy)


def test_best_fit_extends_eclypse_native_strategy() -> None:
    assert issubclass(BestFitStrategy, EclypseBestFitStrategy)


def test_round_robin_uses_eclypse_native_when_no_anchor_extension_is_needed(
    monkeypatch,
) -> None:
    calls = []

    def fake_eclypse_round_robin(self, infrastructure, application, placements, placement_view):
        del self, placements
        calls.append((infrastructure.id, list(application.nodes), type(placement_view).__name__))
        return {"svc-a": "node-a", "svc-b": "node-b"}

    monkeypatch.setattr(EclypseRoundRobinStrategy, "place", fake_eclypse_round_robin)

    strategy = RoundRobinStrategy()
    infrastructure = _infrastructure(["node-a", "node-b"])
    application = _application({"svc-a": {}, "svc-b": {}}, [])

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert mapping == {"svc-a": "node-a", "svc-b": "node-b"}
    assert calls == [("test_infra", ["svc-a", "svc-b"], "PlacementView")]


def test_round_robin_places_anchored_service_on_metadata_matched_layer_node() -> None:
    strategy = RoundRobinStrategy()
    infrastructure = _infrastructure(
        {
            "satellite-42": {"placement_layers": ["space"]},
            "Edge_1": {"placement_layers": ["edge"]},
        }
    )
    application = _application()

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert mapping == {"source_a": "satellite-42"}


def test_layer_matching_requires_explicit_eclypse_symbolic_assets() -> None:
    strategy = RoundRobinStrategy()
    infrastructure = _infrastructure(
        {
            "Space_0": {},
            "Edge_0": {},
        }
    )
    application = _application(
        {
            "source_a": {
                "placement_layers": ["space"],
            }
        }
    )

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert mapping == {}


@pytest.mark.parametrize(
    "strategy",
    [
        BestFitStrategy(),
        MinCTStrategy(),
        NearestFeasibleEdgeStrategy(),
        CostGreedyStrategy(),
        DependencyAwareStrategy(),
    ],
)
def test_custom_strategies_consume_eclypse_residual_node_resources(strategy) -> None:
    infrastructure = _resource_infrastructure(
        {
            "edge-a": {"placement_layers": ["edge"], "cpu": 4.0, "ram": 4.0, "storage": 4.0},
            "edge-b": {"placement_layers": ["edge"], "cpu": 4.0, "ram": 4.0, "storage": 4.0},
        }
    )
    application = _resource_application(
        {
            "svc-a": {"cpu": 4.0, "ram": 1.0, "storage": 1.0},
            "svc-b": {"cpu": 4.0, "ram": 1.0, "storage": 1.0},
        }
    )

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert set(mapping) == {"svc-a", "svc-b"}
    assert set(mapping.values()) == {"edge-a", "edge-b"}


def test_best_fit_skips_resource_fit_when_eclypse_path_is_missing() -> None:
    strategy = BestFitStrategy()
    infrastructure = _resource_infrastructure(
        {
            "iot-a": {"cpu": 3.0, "ram": 3.0, "storage": 3.0},
            "edge-a": {"cpu": 8.0, "ram": 8.0, "storage": 8.0},
            "space-a": {"cpu": 2.0, "ram": 2.0, "storage": 2.0},
        }
    )
    infrastructure.add_edge(
        "iot-a",
        "edge-a",
        strict=False,
        latency=1.0,
        availability=1.0,
        bandwidth=10.0,
    )
    application = _resource_application(
        {
            "source": {"cpu": 3.0, "ram": 3.0, "storage": 3.0},
            "worker": {"cpu": 1.0, "ram": 1.0, "storage": 1.0},
        }
    )
    application.add_edge("source", "worker", strict=False)

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert mapping == {"source": "iot-a", "worker": "edge-a"}


def test_nearest_feasible_edge_prefers_edge_nodes_when_available() -> None:
    strategy = NearestFeasibleEdgeStrategy()
    infrastructure = _infrastructure(
        {
            "iot-a": {"placement_layers": ["iot"]},
            "edge-a": {"placement_layers": ["edge"]},
            "cloud-a": {"placement_layers": ["cloud"]},
        },
        latencies={
            ("iot-a", "edge-a"): 1.0,
            ("edge-a", "cloud-a"): 1.0,
        },
    )

    mapping = strategy.place(
        infrastructure,
        _tiered_application(),
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert mapping == {"source": "iot-a", "worker": "edge-a", "sink": "cloud-a"}


def test_cost_greedy_prefers_edge_when_transfer_cost_outweighs_cloud_compute_savings() -> None:
    strategy = CostGreedyStrategy()
    infrastructure = _infrastructure(
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
    )
    application = _cost_sensitive_application()

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )
    min_ct_mapping = MinCTStrategy().place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert min_ct_mapping["worker"] != "edge-a"
    assert mapping == {"source": "iot-a", "worker": "edge-a", "sink": "cloud-a"}


def test_dependency_aware_uses_anchor_frontier_to_pull_multi_stage_bridge_onto_edge() -> None:
    strategy = DependencyAwareStrategy()
    infrastructure = _infrastructure(
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
    )
    application = _multi_stage_bridge_application()

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )
    min_ct_mapping = MinCTStrategy().place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert min_ct_mapping["bridge_a"] == "iot-a"
    assert mapping == {
        "source": "iot-a",
        "bridge_a": "edge-a",
        "bridge_b": "edge-a",
        "sink": "cloud-a",
    }


def test_dependency_aware_keeps_one_sided_anchor_chain_on_edge_frontier() -> None:
    strategy = DependencyAwareStrategy()
    infrastructure = _infrastructure(
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
    )
    application = _one_sided_anchor_chain_application()

    mapping = strategy.place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )
    min_ct_mapping = MinCTStrategy().place(
        infrastructure,
        application,
        placements={},
        placement_view=_placement_view(infrastructure),
    )

    assert min_ct_mapping["stage_a"] == "iot-a"
    assert mapping == {
        "source": "iot-a",
        "stage_a": "edge-a",
        "stage_b": "edge-a",
        "stage_c": "edge-a",
    }


def test_eo_downstream_stages_are_placeable_across_feasible_layers() -> None:
    scenario = build_scenario("eo_dual_stream", config={}, seed=7)
    application = scenario.application

    fusion_id = _service_id_with_suffix(application, "_Fusion")
    triage_id = _service_id_with_suffix(application, "_Triage")
    fast_path_id = _service_id_with_suffix(application, "_FastPath")
    bulk_worker_id = _service_id_with_suffix(application, "_BulkPath_worker_0")

    assert application.nodes[fusion_id]["placement_layers"] == []
    assert application.nodes[triage_id]["placement_layers"] == []
    assert application.nodes[fast_path_id]["placement_layers"] == []
    assert application.nodes[bulk_worker_id]["placement_layers"] == []

    probe_infrastructure = _infrastructure(
        {
            "space-a": {"placement_layers": ["space"]},
            "iot-a": {"placement_layers": ["iot"]},
            "edge-a": {"placement_layers": ["edge"]},
            "cloud-a": {"placement_layers": ["cloud"]},
        },
        latencies={
            ("space-a", "iot-a"): 1.0,
            ("iot-a", "cloud-a"): 1.0,
            ("cloud-a", "edge-a"): 1.0,
            ("edge-a", "space-a"): 1.0,
        },
    )
    probe_application = _application(
        {
            "SpaceSource": {"placement_layers": ["space"]},
            "IoTSource": {"placement_layers": ["iot"]},
            "Fusion": {},
            "BulkPath_worker_0": {},
        },
        edges=[
            ("SpaceSource", "Fusion"),
            ("IoTSource", "Fusion"),
            ("Fusion", "BulkPath_worker_0"),
        ],
    )

    mapping = RoundRobinStrategy().place(
        probe_infrastructure,
        probe_application,
        placements={},
        placement_view=None,
    )

    assert mapping["SpaceSource"] == "space-a"
    assert mapping["IoTSource"] == "iot-a"
    assert mapping["Fusion"] == "edge-a"
    assert mapping["BulkPath_worker_0"] == "cloud-a"


@pytest.mark.parametrize(
    "strategy",
    [
        BestFitStrategy(),
        MinCTStrategy(),
        NearestFeasibleEdgeStrategy(),
        CostGreedyStrategy(),
        DependencyAwareStrategy(),
    ],
)
def test_strategy_mappings_are_audited_through_eclypse_placement_lifecycle(strategy) -> None:
    scenario = build_scenario("split_inference", config={}, seed=7)
    adapter = EclypsePlacementAdapter(scenario.infrastructure, scenario.application)
    active_services = [
        service_id
        for service_id in scenario.application.nodes
        if service_id not in scenario.worker_pools["CloudRefine"][1:]
    ]

    mapping = adapter.plan_mapping(strategy=strategy, active_services=active_services)

    assert set(mapping) == set(active_services)


def test_runtime_adapter_rejects_eclypse_reset_when_interaction_path_is_missing() -> None:
    infrastructure = _resource_infrastructure(
        {
            "node-a": {"cpu": 4.0, "ram": 4.0, "storage": 4.0},
            "node-b": {"cpu": 4.0, "ram": 4.0, "storage": 4.0},
        }
    )
    application = _resource_application(
        {
            "svc-a": {"cpu": 1.0, "ram": 1.0, "storage": 1.0},
            "svc-b": {"cpu": 1.0, "ram": 1.0, "storage": 1.0},
        }
    )
    application.add_edge("svc-a", "svc-b", strict=False)
    adapter = EclypsePlacementAdapter(infrastructure, application)

    with pytest.raises(ValueError, match="service interaction path is not available"):
        adapter.sync_mapping(
            {"svc-a": "node-a", "svc-b": "node-b"},
            active_services=["svc-a", "svc-b"],
            force=True,
        )
