import pytest

pytest.importorskip("eclypse")  # the MILP-oracle feasibility tests need the external eclypse substrate

from continuum_bench.oracles import OracleResult, solve_small_instance


def _scipy_milp_available() -> bool:
    try:
        from scipy.optimize import milp  # noqa: F401
    except Exception:
        return False
    return True


SCIPY_MILP_AVAILABLE = _scipy_milp_available()


def _small_snapshot() -> dict:
    return {
        "services": {
            "camera": {"demand": 0, "allowed_nodes": ["iot-a"]},
            "infer": {
                "demand": 2,
                "allowed_nodes": ["edge-a", "cloud-a"],
                "node_costs": {"edge-a": 1.0, "cloud-a": 3.0},
            },
            "archive": {"demand": 0, "allowed_nodes": ["cloud-a"]},
        },
        "nodes": {
            "iot-a": {"capacity": 0},
            "edge-a": {
                "capacity": 2,
                "availability": 0.80,
                "dynamic_power_w_per_cpu": 10.0,
            },
            "cloud-a": {
                "capacity": 4,
                "availability": 0.99,
                "dynamic_power_w_per_cpu": 1.0,
            },
        },
        "flows": [
            {"source": "camera", "target": "infer", "weight": 5.0},
            {"source": "infer", "target": "archive", "weight": 1.0},
        ],
        "links": {
            ("iot-a", "edge-a"): 1.0,
            ("edge-a", "iot-a"): 1.0,
            ("edge-a", "cloud-a"): 4.0,
            ("cloud-a", "edge-a"): 4.0,
            ("iot-a", "cloud-a"): 9.0,
            ("cloud-a", "iot-a"): 9.0,
        },
    }


def _assignment_is_feasible(snapshot: dict, placement: dict[str, str]) -> bool:
    if set(placement) != set(snapshot["services"]):
        return False

    used_capacity = {node_id: 0 for node_id in snapshot["nodes"]}
    for service_id, service in snapshot["services"].items():
        node_id = placement.get(service_id)
        if node_id not in service["allowed_nodes"]:
            return False
        used_capacity[node_id] += int(service.get("demand", 0))

    for node_id, node in snapshot["nodes"].items():
        if used_capacity[node_id] > int(node.get("capacity", 0)):
            return False
    return True


def test_solve_small_instance_accepts_reduced_snapshot() -> None:
    result = solve_small_instance(_small_snapshot(), objective="latency")

    assert isinstance(result, OracleResult)
    assert result.method in {"exact_enumeration", "milp_scipy_highs"}


@pytest.mark.skipif(not SCIPY_MILP_AVAILABLE, reason="scipy MILP backend is not installed")
def test_solve_small_instance_can_use_solver_backed_milp() -> None:
    result = solve_small_instance(_small_snapshot(), objective="latency", backend="milp")

    assert result.method == "milp_scipy_highs"
    assert result.solver_status == "optimal"
    assert result.optimality_gap == pytest.approx(0.0)
    assert result.placement == {
        "camera": "iot-a",
        "infer": "edge-a",
        "archive": "cloud-a",
    }
    assert result.objective_value == pytest.approx(10.0)


@pytest.mark.skipif(not SCIPY_MILP_AVAILABLE, reason="scipy MILP backend is not installed")
def test_milp_backend_uses_energy_objective_when_linear() -> None:
    result = solve_small_instance(
        _small_snapshot(),
        objective={"weights": {"latency": 0.0, "energy": 1.0}},
        backend="milp",
    )

    assert result.method == "milp_scipy_highs"
    assert result.placement["infer"] == "cloud-a"
    assert result.objective_value == pytest.approx(2.0)


def test_auto_backend_falls_back_for_non_linear_objective() -> None:
    result = solve_small_instance(
        _small_snapshot(),
        objective={"weights": {"latency": 0.0, "failure_risk": 1.0}},
        backend="auto",
    )

    assert result.method == "exact_enumeration"
    assert result.placement["infer"] == "cloud-a"


@pytest.mark.skipif(not SCIPY_MILP_AVAILABLE, reason="scipy MILP backend is not installed")
def test_milp_backend_rejects_non_linear_objective() -> None:
    with pytest.raises(RuntimeError, match="failure_risk uses enumeration"):
        solve_small_instance(
            _small_snapshot(),
            objective={"weights": {"failure_risk": 1.0}},
            backend="milp",
        )


def test_solve_small_instance_produces_feasible_assignment() -> None:
    snapshot = _small_snapshot()

    result = solve_small_instance(snapshot)

    assert _assignment_is_feasible(snapshot, result.placement)
    assert result.placement == {
        "camera": "iot-a",
        "infer": "edge-a",
        "archive": "cloud-a",
    }


def test_solve_small_instance_reports_objective_and_gap_fields() -> None:
    result = solve_small_instance(_small_snapshot(), backend="enumeration")

    assert isinstance(result.objective_value, float)
    assert result.objective_value == 10.0
    assert result.solver_status == "optimal"
    assert result.optimality_gap == 0.0
    assert result.method == "exact_enumeration"


def test_weighted_objective_can_optimize_energy_instead_of_latency_only() -> None:
    snapshot = _small_snapshot()

    result = solve_small_instance(
        snapshot,
        objective={
            "weights": {
                "latency": 0.0,
                "energy": 1.0,
            }
        },
    )

    assert result.placement["infer"] == "cloud-a"
    assert result.objective_value == 2.0


def test_weighted_objective_can_optimize_reliability_risk() -> None:
    snapshot = _small_snapshot()

    result = solve_small_instance(
        snapshot,
        objective={
            "weights": {
                "latency": 0.0,
                "failure_risk": 1.0,
            }
        },
    )

    assert result.placement["infer"] == "cloud-a"
    assert result.objective_value < 0.02


def test_oracle_feasibility_uses_eclypse_anchor_assets() -> None:
    snapshot = _small_snapshot()
    snapshot["services"]["camera"] = {
        "cpu": 0.0,
        "allowed_nodes": ["iot-a", "cloud-a"],
        "placement_layers": ["iot"],
    }
    snapshot["nodes"]["iot-a"]["placement_layers"] = ["iot"]
    snapshot["nodes"]["edge-a"]["placement_layers"] = ["edge"]
    snapshot["nodes"]["cloud-a"]["placement_layers"] = ["cloud"]

    result = solve_small_instance(snapshot, objective="latency")

    assert result.placement["camera"] == "iot-a"


def test_oracle_does_not_infer_layer_assets_from_node_names() -> None:
    snapshot = _small_snapshot()
    snapshot["services"]["camera"] = {
        "cpu": 0.0,
        "allowed_nodes": ["iot-a", "cloud-a"],
        "placement_layers": ["iot"],
    }

    result = solve_small_instance(snapshot, objective="latency")

    assert result.solver_status == "infeasible"
    assert result.placement == {}


def test_oracle_treats_missing_eclypse_paths_as_infeasible() -> None:
    snapshot = _small_snapshot()
    del snapshot["links"][("iot-a", "edge-a")]

    result = solve_small_instance(snapshot, objective="latency")

    assert result.placement["infer"] == "cloud-a"
