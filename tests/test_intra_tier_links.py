"""Same-tier nodes are directly reachable when intra-tier links are configured.

Without these links the tiered generator wires each tier only to the tier above, so two
edge nodes communicate through the cloud and an edge-local placement pays two WAN crossings
per hop. The eight scenario configs mesh their edge tier over a LAN (1 ms, 10 Gbps), as the
ContinuumBench paper describes; a config without ``intra_tier`` keeps the tiered graph alone.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from continuum_bench.infra import build_extended_continuum

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
CONFIG = CONFIG_DIR / "split_inference_small.yaml"
SCENARIO_CONFIGS = [
    "eo_small", "eo_trace_small", "driving_small", "driving_trace_small",
    "split_inference_small", "split_inference_trace_small",
    "industrial_iot_small", "industrial_iot_trace_small",
]


def _infra(cfg):
    return build_extended_continuum(cfg, seed=0).infrastructure


def _edge_pairs(infra):
    return [(u, v) for u, v, _ in infra.edges(data=True)
            if u.startswith("Edge") and v.startswith("Edge")]


def test_absent_without_intra_tier():
    """A config without intra_tier keeps the tiered graph: edge nodes meet in the cloud."""
    cfg = copy.deepcopy(yaml.safe_load(CONFIG.read_text())["infrastructure"])
    cfg.pop("intra_tier", None)
    infra = _infra(cfg)
    assert _edge_pairs(infra) == []
    hops, _ = infra.path("Edge_1", "Edge_0")
    assert len(hops) == 2  # Edge_1 -> Cloud_0 -> Edge_0


def test_direct_path_when_enabled():
    cfg = copy.deepcopy(yaml.safe_load(CONFIG.read_text())["infrastructure"])
    cfg["intra_tier"] = {"edge": {"latency": 1.0, "bandwidth": 10000.0}}
    infra = _infra(cfg)
    assert _edge_pairs(infra)
    hops, _ = infra.path("Edge_1", "Edge_0")
    assert len(hops) == 1
    assert hops[0][2]["latency"] == 1.0


def test_single_node_tier_is_a_no_op():
    """A tier with one node has no pair to mesh."""
    cfg = copy.deepcopy(yaml.safe_load(CONFIG.read_text())["infrastructure"])
    cfg["intra_tier"] = {"cloud": {"latency": 1.0, "bandwidth": 10000.0}}
    infra = _infra(cfg)
    assert [(u, v) for u, v, _ in infra.edges(data=True)
            if u.startswith("Cloud") and v.startswith("Cloud")] == []


def test_every_scenario_config_meshes_its_edge_tier():
    for name in SCENARIO_CONFIGS:
        infra = _infra(yaml.safe_load((CONFIG_DIR / f"{name}.yaml").read_text())["infrastructure"])
        hops, _ = infra.path("Edge_1", "Edge_0")
        assert len(hops) == 1, f"{name}: the edge nodes are not directly linked"
        assert hops[0][2]["latency"] == 1.0, f"{name}: the edge link is not the 1 ms LAN"
