"""Same-tier nodes must be directly reachable when intra-tier links are configured.

Without them the tiered generator wires each tier only to the tier above, so two edge
nodes communicate through the cloud: an edge-local placement pays two WAN crossings per
hop and is penalized. The links are off by default, so the version 1.0 scenarios keep their
original topology.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from continuum_bench.infra import build_extended_continuum

CONFIG = Path(__file__).resolve().parents[1] / "configs" / "split_inference_small.yaml"


def _infra(cfg):
    return build_extended_continuum(cfg, seed=0).infrastructure


def _edge_pairs(infra):
    return [(u, v) for u, v, _ in infra.edges(data=True)
            if u.startswith("Edge") and v.startswith("Edge")]


def test_absent_by_default():
    """Opt-in: a config without intra_tier keeps the original topology."""
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
