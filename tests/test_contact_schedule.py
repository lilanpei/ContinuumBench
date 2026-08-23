from __future__ import annotations

import json

import pytest

from continuum_bench._eclypse import ensure_eclypse_on_path
from continuum_bench.infra import (
    ContactSchedule,
    ContactScheduleUpdatePolicy,
)

ensure_eclypse_on_path()
from eclypse.graph import Infrastructure


def _infrastructure() -> Infrastructure:
    infrastructure = Infrastructure(
        infrastructure_id="contact_trace_test",
        include_default_assets=False,
        seed=0,
    )
    for node_id in ["Space_0", "Edge_0", "Edge_1"]:
        infrastructure.add_node(node_id, strict=False, availability=1.0)
    infrastructure.add_edge(
        "Space_0",
        "Edge_0",
        symmetric=True,
        strict=False,
        availability=0.0,
        bandwidth=0.0,
        latency=220.0,
    )
    infrastructure.add_edge(
        "Space_0",
        "Edge_1",
        symmetric=True,
        strict=False,
        availability=0.0,
        bandwidth=0.0,
        latency=220.0,
    )
    return infrastructure


def test_contact_schedule_loads_per_link_csv_trace(tmp_path) -> None:
    trace_path = tmp_path / "contacts.csv"
    trace_path.write_text(
        "\n".join(
            [
                "start_s,end_s,src,dst,bandwidth_mbps,latency_ms,contact_open",
                "0,5,Space_0,Edge_0,25,180,1",
                "5,8,Space_0,Edge_0,0,220,0",
                "2,6,Space_0,Edge_1,10,240,1",
            ]
        ),
        encoding="utf-8",
    )

    schedule = ContactSchedule.from_config({"mode": "trace", "path": str(trace_path)})

    assert schedule.link_state(1.0, "Space_0", "Edge_0") == (True, 25.0)
    assert schedule.edge_updates(1.0, "Space_0", "Edge_0")["contact_latency"] == pytest.approx(180.0)
    assert schedule.link_state(6.0, "Space_0", "Edge_0") == (False, 0.0)
    assert schedule.link_state(3.0, "Space_0", "Edge_1") == (True, 10.0)
    assert schedule.link_state(7.0, "Space_0", "Edge_1") == (False, 0.0)
    assert schedule.trace_edges() == [
        ("Edge_0", "Space_0"),
        ("Edge_1", "Space_0"),
        ("Space_0", "Edge_0"),
        ("Space_0", "Edge_1"),
    ]


def test_contact_schedule_can_repeat_short_trace_patterns_for_long_runs(tmp_path) -> None:
    trace_path = tmp_path / "contacts.csv"
    trace_path.write_text(
        "\n".join(
            [
                "start_s,end_s,src,dst,bandwidth_mbps,latency_ms,contact_open",
                "0,5,Space_0,Edge_0,25,180,1",
                "5,10,Space_0,Edge_0,0,220,0",
            ]
        ),
        encoding="utf-8",
    )

    no_repeat = ContactSchedule.from_config({"mode": "trace", "path": str(trace_path)})
    repeated = ContactSchedule.from_config(
        {"mode": "trace", "path": str(trace_path), "repeat": True}
    )

    assert no_repeat.link_state(12.0, "Space_0", "Edge_0") == (False, 0.0)
    assert repeated.link_state(12.0, "Space_0", "Edge_0") == (True, 25.0)
    assert repeated.edge_updates(17.0, "Space_0", "Edge_0") == {
        "contact_open": 0.0,
        "contact_bandwidth": 0.0,
        "contact_latency": 220.0,
    }


def test_contact_trace_rejects_legacy_availability_column(tmp_path) -> None:
    trace_path = tmp_path / "legacy_contacts.csv"
    trace_path.write_text(
        "\n".join(
            [
                "start_s,end_s,src,dst,bandwidth_mbps,latency_ms,availability",
                "0,5,Space_0,Edge_0,25,180,1",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="contact_open"):
        ContactSchedule.from_config({"mode": "trace", "path": str(trace_path)})


def test_contact_schedule_update_policy_uses_trace_per_link_updates(tmp_path) -> None:
    trace_path = tmp_path / "contacts.json"
    trace_path.write_text(
        json.dumps(
            [
                {
                    "start_s": 0,
                    "end_s": 3,
                    "src": "Space_0",
                    "dst": "Edge_0",
                    "bandwidth_mbps": 15,
                    "latency_ms": 111,
                    "contact_open": 1,
                },
                {
                    "start_s": 0,
                    "end_s": 3,
                    "src": "Space_0",
                    "dst": "Edge_1",
                    "bandwidth_mbps": 0,
                    "latency_ms": 222,
                    "contact_open": 0,
                },
            ]
        ),
        encoding="utf-8",
    )
    infrastructure = _infrastructure()
    schedule = ContactSchedule.from_config(
        {"mode": "trace", "path": str(trace_path), "symmetric": True}
    )
    policy = ContactScheduleUpdatePolicy(
        schedule=schedule,
        contact_edges=[("Space_0", "Edge_0"), ("Space_0", "Edge_1")],
    )
    infrastructure.edge_update_policy.append(policy.apply_edges)

    policy.set_time(1.0)
    infrastructure.evolve()
    updates = policy.last_updates

    assert updates[("Space_0", "Edge_0")] == {
        "contact_open": 1.0,
        "contact_bandwidth": 15.0,
        "contact_latency": 111.0,
    }
    assert updates[("Edge_0", "Space_0")] == {
        "contact_open": 1.0,
        "contact_bandwidth": 15.0,
        "contact_latency": 111.0,
    }
    assert updates[("Space_0", "Edge_1")]["contact_open"] == 0.0
    assert infrastructure.edges["Space_0", "Edge_0"]["latency"] == pytest.approx(220.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["availability"] == pytest.approx(0.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["contact_latency"] == pytest.approx(111.0)


def test_contact_schedule_update_policy_runs_through_eclypse_evolve(tmp_path) -> None:
    trace_path = tmp_path / "contacts.csv"
    trace_path.write_text(
        "\n".join(
            [
                "start_s,end_s,src,dst,bandwidth_mbps,latency_ms,contact_open",
                "0,3,Space_0,Edge_0,15,111,1",
                "0,3,Space_0,Edge_1,0,222,0",
            ]
        ),
        encoding="utf-8",
    )
    infrastructure = _infrastructure()
    schedule = ContactSchedule.from_config(
        {"mode": "trace", "path": str(trace_path), "symmetric": True}
    )
    policy = ContactScheduleUpdatePolicy(
        schedule=schedule,
        contact_edges=[("Space_0", "Edge_0"), ("Space_0", "Edge_1")],
    )
    infrastructure.edge_update_policy.append(policy.apply_edges)

    policy.set_time(1.0)
    infrastructure.evolve()

    assert infrastructure.edges["Space_0", "Edge_0"]["availability"] == pytest.approx(0.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["bandwidth"] == pytest.approx(0.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["latency"] == pytest.approx(220.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["contact_open"] == pytest.approx(1.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["contact_bandwidth"] == pytest.approx(15.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["contact_latency"] == pytest.approx(111.0)
    assert infrastructure.edges["Edge_0", "Space_0"]["contact_latency"] == pytest.approx(111.0)
    assert infrastructure.edges["Space_0", "Edge_1"]["contact_open"] == pytest.approx(0.0)
    assert policy.last_updates[("Space_0", "Edge_0")]["contact_latency"] == pytest.approx(111.0)


def test_trace_update_without_latency_restores_base_edge_latency(tmp_path) -> None:
    trace_path = tmp_path / "contacts.csv"
    trace_path.write_text(
        "\n".join(
            [
                "start_s,end_s,src,dst,bandwidth_mbps,latency_ms,contact_open",
                "0,3,Space_0,Edge_0,15,111,1",
                "3,6,Space_0,Edge_0,25,,1",
            ]
        ),
        encoding="utf-8",
    )
    infrastructure = _infrastructure()
    schedule = ContactSchedule.from_config(
        {"mode": "trace", "path": str(trace_path), "symmetric": False}
    )
    policy = ContactScheduleUpdatePolicy(
        schedule=schedule,
        contact_edges=[("Space_0", "Edge_0")],
    )
    infrastructure.edge_update_policy.append(policy.apply_edges)

    policy.set_time(1.0)
    infrastructure.evolve()
    assert infrastructure.edges["Space_0", "Edge_0"]["latency"] == pytest.approx(220.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["contact_latency"] == pytest.approx(111.0)

    policy.set_time(4.0)
    infrastructure.evolve()

    assert policy.last_updates[("Space_0", "Edge_0")]["contact_latency"] == pytest.approx(220.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["latency"] == pytest.approx(220.0)
    assert infrastructure.edges["Space_0", "Edge_0"]["contact_latency"] == pytest.approx(220.0)
