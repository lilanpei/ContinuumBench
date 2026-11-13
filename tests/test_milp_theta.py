"""The MILP oracle under theta: tier-priced cost term, egress budget and energy cap."""

from __future__ import annotations

import pytest

pytest.importorskip("eclypse")

from continuum_bench.oracles import solve_small_instance


def _snapshot(*, edge_price: float = 1.0, cloud_price: float = 1.0) -> dict:
    # infer may sit on the edge (latency 1) or in the cloud (latency 3). Everything else is pinned.
    return {
        "services": {
            "camera": {"demand": 0, "allowed_nodes": ["iot-a"], "placement_layers": ["iot"]},
            "infer": {
                "demand": 2,
                "allowed_nodes": ["edge-a", "cloud-a"],
                "node_costs": {"edge-a": 1.0, "cloud-a": 3.0},
            },
            "archive": {"demand": 0, "allowed_nodes": ["cloud-a"], "placement_layers": ["cloud"]},
        },
        "nodes": {
            "iot-a": {"capacity": 0, "placement_layers": ["iot"]},
            "edge-a": {"capacity": 2, "placement_layers": ["edge"], "price": edge_price, "dynamic_power_w_per_cpu": 10.0},
            "cloud-a": {"capacity": 4, "placement_layers": ["cloud"], "price": cloud_price, "dynamic_power_w_per_cpu": 1.0},
        },
        "flows": [
            {"source": "camera", "target": "infer", "weight": 1.0, "volume_mb": 4.0},
            {"source": "infer", "target": "archive", "weight": 1.0, "volume_mb": 0.5},
        ],
        "links": {
            ("iot-a", "edge-a"): 1.0, ("edge-a", "iot-a"): 1.0,
            ("edge-a", "cloud-a"): 4.0, ("cloud-a", "edge-a"): 4.0,
            ("iot-a", "cloud-a"): 9.0, ("cloud-a", "iot-a"): 9.0,
        },
    }


@pytest.mark.parametrize("backend", ["enumeration", "milp"])
def test_cost_weight_with_expensive_edge_moves_infer_to_cloud(backend):
    if backend == "milp":
        pytest.importorskip("scipy")
    latency_first = solve_small_instance(_snapshot(edge_price=4.0), objective="latency", backend=backend)
    assert latency_first.placement["infer"] == "edge-a"
    cost_first = solve_small_instance(
        _snapshot(edge_price=4.0),
        objective={"weights": {"latency": 1.0, "cost": 10.0}},
        backend=backend,
    )
    assert cost_first.placement["infer"] == "cloud-a"


@pytest.mark.parametrize("backend", ["enumeration", "milp"])
def test_egress_budget_keeps_the_large_flow_off_the_cloud(backend):
    if backend == "milp":
        pytest.importorskip("scipy")
    snapshot = _snapshot(edge_price=4.0)
    snapshot["constraints"] = {"egress_budget_mb": 1.0}  # camera->infer (4 MB) may not enter the cloud
    result = solve_small_instance(snapshot, objective={"weights": {"latency": 1.0, "cost": 10.0}}, backend=backend)
    assert result.solver_status == "optimal"
    assert result.placement["infer"] == "edge-a"


@pytest.mark.parametrize("backend", ["enumeration", "milp"])
def test_energy_cap_forbids_the_power_hungry_edge(backend):
    if backend == "milp":
        pytest.importorskip("scipy")
    snapshot = _snapshot()
    snapshot["constraints"] = {"energy_cap_w": 5.0}  # infer on edge = 20 W, on cloud = 2 W
    result = solve_small_instance(snapshot, objective="latency", backend=backend)
    assert result.placement["infer"] == "cloud-a"
    snapshot["constraints"] = {"energy_cap_w": 1.0}
    infeasible = solve_small_instance(snapshot, objective="latency", backend=backend)
    assert infeasible.solver_status != "optimal" and infeasible.placement == {}


def test_energy_terms_ignore_idle_power_which_does_not_depend_on_the_placement():
    from continuum_bench.oracles.milp_oracle import _assignment_energy, _energy_objective

    snapshot = _snapshot()
    for node in snapshot["nodes"].values():
        node["idle_power_w"] = 500.0  # constant of the infrastructure, must not enter the cap
    placement = {"camera": "iot-a", "infer": "edge-a", "archive": "cloud-a"}
    assert _energy_objective(snapshot, placement) == 20.0  # infer: 2 cores x 10 W on the edge
    assert _assignment_energy(snapshot["services"]["infer"], snapshot["nodes"]["cloud-a"]) == 2.0
    snapshot["constraints"] = {"energy_cap_w": 5.0}
    result = solve_small_instance(snapshot, objective="latency", backend="milp")
    assert result.solver_status == "optimal" and result.placement["infer"] == "cloud-a"


def test_milp_snapshot_keeps_unanchored_stages_off_the_source_tiers():
    import networkx as nx
    from types import SimpleNamespace
    from continuum_bench.controllers.placement_only import _build_milp_snapshot

    infra = nx.Graph()
    infra.add_node("iot0", cpu=4.0, placement_layers=["iot"])
    infra.add_node("edge0", cpu=12.0, placement_layers=["edge"])
    infra.add_node("cloud0", cpu=24.0, placement_layers=["cloud"])
    infra.add_edge("iot0", "edge0", latency=5.0, bandwidth=100.0)
    infra.add_edge("edge0", "cloud0", latency=50.0, bandwidth=40.0)
    app = nx.DiGraph()
    app.add_node("IoTSource", cpu=0.5, placement_layers=["iot"])
    app.add_node("Triage", cpu=2.0)
    app.add_edge("IoTSource", "Triage")
    state = SimpleNamespace(infrastructure=infra, application=app, placement={}, metadata={"milp_objective": {"weights": {"latency": 1.0, "cost": 1.0}, "tier_prices": {"edge": 4.0, "cloud": 1.0}, "residency": "no_cloud"}})
    snap = _build_milp_snapshot(state, ["IoTSource", "Triage"])
    assert "iot0" in snap["services"]["IoTSource"]["allowed_nodes"]          # anchored source stays on its tier
    assert snap["services"]["Triage"]["allowed_nodes"] == ["edge0"]           # compute tier only, cloud excluded by residency
    state.metadata["milp_objective"]["residency"] = "any"
    snap = _build_milp_snapshot(state, ["IoTSource", "Triage"])
    assert sorted(snap["services"]["Triage"]["allowed_nodes"]) == ["cloud0", "edge0"]
