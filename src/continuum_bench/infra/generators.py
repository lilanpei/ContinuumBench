from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from continuum_bench.calibration_profiles import (
    load_selected_energy_profile,
    load_selected_network_profile,
)
from continuum_bench._eclypse import ensure_eclypse_on_path


def _continuum_infrastructure_class() -> Any:
    """Return the Eclypse Infrastructure subclass used by ContinuumBench."""

    ensure_eclypse_on_path()
    try:
        from eclypse.graph import Infrastructure
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Eclypse is required for ContinuumBench infrastructure graphs. "
            "Install eclypse or set ECLYPSE_HOME to a local checkout."
        ) from exc

    return Infrastructure


def _build_infrastructure(infrastructure_id: str, seed: int) -> Any:
    """Instantiate an Eclypse infrastructure graph for benchmark scenarios."""

    ensure_eclypse_on_path()
    try:
        from eclypse.graph.assets import Additive
        from eclypse.graph.assets import Symbolic
        from eclypse.graph.assets.defaults import (
            bandwidth,
            cpu,
            get_default_path_aggregators,
            latency,
            ram,
            storage,
        )
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Eclypse is required for ContinuumBench infrastructure graphs. "
            "Install eclypse or set ECLYPSE_HOME to a local checkout."
        ) from exc

    ContinuumBenchInfrastructure = _continuum_infrastructure_class()

    return ContinuumBenchInfrastructure(
        infrastructure_id=infrastructure_id,
        include_default_assets=False,
        node_assets={
            "cpu": cpu(init_fn_or_value=0.0),
            "ram": ram(init_fn_or_value=0.0),
            "storage": storage(init_fn_or_value=0.0),
            "idle_power_w": Additive(0.0, float("inf"), init_fn_or_value=0.0, functional=False),
            "dynamic_power_w_per_cpu": Additive(0.0, float("inf"), init_fn_or_value=0.0, functional=False),
            "dynamic_power_w_per_ram": Additive(0.0, float("inf"), init_fn_or_value=0.0, functional=False),
            "dynamic_power_w_per_storage": Additive(0.0, float("inf"), init_fn_or_value=0.0, functional=False),
            "battery_capacity_j": Additive(0.0, float("inf"), init_fn_or_value=0.0, functional=False),
            "battery_remaining_j": Additive(0.0, float("inf"), init_fn_or_value=0.0, functional=False),
            "placement_layers": Symbolic(
                lower_bound=[],
                upper_bound=["space", "iot", "edge", "cloud"],
                init_fn_or_value=[],
            ),
        },
        edge_assets={
            "latency": latency(init_fn_or_value=0.0),
            "bandwidth": bandwidth(init_fn_or_value=0.0),
            "energy_j_per_byte": Additive(0.0, float("inf"), init_fn_or_value=0.0, functional=False),
        },
        path_assets_aggregators={
            **get_default_path_aggregators(),
            "energy_j_per_byte": lambda values: sum(list(values)),
        },
        seed=seed,
    )


@dataclass
class InfrastructureBundle:
    infrastructure: Any
    contact_edges: List[Tuple[str, str]]


def build_extended_continuum(config: Dict[str, Any], seed: int) -> InfrastructureBundle:
    """Build a 4-layer continuum graph with contact-scheduled space links."""

    n_space = _read_count(config, "n_space", default=1, min_value=0)
    n_iot = _read_count(config, "n_iot", default=2, min_value=0)
    n_edge = _read_count(config, "n_edge", default=2, min_value=1)
    n_cloud = _read_count(config, "n_cloud", default=1, min_value=0)

    infra = _build_infrastructure(
        infrastructure_id=str(config.get("infrastructure_id", "continuum_bench_infra")),
        seed=seed,
    )

    cloud_profile = _with_defaults(
        config.get("cloud", {}),
        {
            "cpu": 128.0,
            "ram": 256.0,
            "storage": 10000.0,
            "availability": 0.999,
            "power": "infinite",
        },
    )
    edge_profile = _with_defaults(
        config.get("edge", {}),
        {
            "cpu": 24.0,
            "ram": 64.0,
            "storage": 2000.0,
            "availability": 0.995,
            "power": "stable",
        },
    )
    iot_profile = _with_defaults(
        config.get("iot", {}),
        {
            "cpu": 4.0,
            "ram": 8.0,
            "storage": 64.0,
            "availability": 0.97,
            "power": "battery_constrained",
        },
    )
    space_profile = _with_defaults(
        config.get("space", {}),
        {
            "cpu": 8.0,
            "ram": 16.0,
            "storage": 512.0,
            "availability": 0.95,
            "power": "solar_battery",
        },
    )
    calibration_cfg = config.get("calibration")
    network_profile = load_selected_network_profile(calibration_cfg)
    energy_profile = load_selected_energy_profile(calibration_cfg)

    cloud_nodes: List[str] = []
    for idx in range(n_cloud):
        node_id = f"Cloud_{idx}"
        infra.add_node(
            node_id,
            strict=False,
            layer="cloud",
            placement_layers=["cloud"],
            **_with_energy_node_profile(cloud_profile, energy_profile, "cloud"),
        )
        cloud_nodes.append(node_id)

    edge_nodes: List[str] = []
    for idx in range(n_edge):
        node_id = f"Edge_{idx}"
        infra.add_node(
            node_id,
            strict=False,
            layer="edge",
            placement_layers=["edge"],
            **_with_energy_node_profile(edge_profile, energy_profile, "edge"),
        )
        edge_nodes.append(node_id)

    iot_nodes: List[str] = []
    for idx in range(n_iot):
        node_id = f"IoT_{idx}"
        infra.add_node(
            node_id,
            strict=False,
            layer="iot",
            placement_layers=["iot"],
            **_with_energy_node_profile(iot_profile, energy_profile, "iot"),
        )
        iot_nodes.append(node_id)

    space_nodes: List[str] = []
    for idx in range(n_space):
        node_id = f"Space_{idx}"
        infra.add_node(
            node_id,
            strict=False,
            layer="space",
            placement_layers=["space"],
            **_with_energy_node_profile(space_profile, energy_profile, "space"),
        )
        space_nodes.append(node_id)

    cloud_edge = _resolve_link_profile(
        config.get("edge_to_cloud", {}),
        {"latency": 20.0, "bandwidth": 1000.0},
        network_profile,
        energy_profile,
        "edge",
        "cloud",
    )
    iot_edge = _resolve_link_profile(
        config.get("iot_to_edge", {}),
        {"latency": 6.0, "bandwidth": 120.0},
        network_profile,
        energy_profile,
        "iot",
        "edge",
    )
    space_edge = _resolve_link_profile(
        config.get("space_to_edge", {}),
        {"latency": 220.0, "bandwidth": 0.0},
        network_profile,
        energy_profile,
        "space",
        "edge",
    )

    for edge_id in edge_nodes:
        for cloud_id in cloud_nodes:
            infra.add_edge(edge_id, cloud_id, symmetric=True, strict=False, **cloud_edge)

    for idx, iot_id in enumerate(iot_nodes):
        target_edge = edge_nodes[idx % len(edge_nodes)]
        infra.add_edge(iot_id, target_edge, symmetric=True, strict=False, **iot_edge)

    contact_edges: List[Tuple[str, str]] = []
    for space_id in space_nodes:
        for edge_id in edge_nodes:
            infra.add_edge(space_id, edge_id, symmetric=True, strict=False, **space_edge)
            contact_edges.append((space_id, edge_id))

    # Optional intra-tier connectivity. Without it, same-tier nodes are only reachable
    # through the tier above -- two edge nodes talk via the cloud, so spreading stages
    # across the edge tier costs two WAN crossings and "keep work local" is penalized
    # rather than rewarded. Off by default: enabling it changes path lengths, and so
    # results, for every existing scenario.
    #
    #   infrastructure:
    #     intra_tier:
    #       edge: {latency: 1.0, bandwidth: 10000.0}
    intra_tier_cfg = config.get("intra_tier", {}) or {}
    tier_nodes = {"cloud": cloud_nodes, "edge": edge_nodes, "iot": iot_nodes, "space": space_nodes}
    for tier, link_cfg in intra_tier_cfg.items():
        nodes = tier_nodes.get(str(tier).lower(), [])
        if not link_cfg or len(nodes) < 2:
            continue
        link = _resolve_link_profile(
            dict(link_cfg), {"latency": 1.0, "bandwidth": 10_000.0},
            network_profile, energy_profile, str(tier), str(tier),
        )
        for i, src in enumerate(nodes):
            for dst in nodes[i + 1:]:
                infra.add_edge(src, dst, symmetric=True, strict=False, **link)

    return InfrastructureBundle(infrastructure=infra, contact_edges=contact_edges)


def _with_defaults(raw: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay a partial mapping onto a default mapping."""

    merged = dict(defaults)
    merged.update(raw or {})
    return merged


def _resolve_link_profile(
    raw_override: Dict[str, Any],
    defaults: Dict[str, Any],
    selected_profile: Dict[str, Any] | None,
    energy_profile: Dict[str, Any] | None,
    source_tier: str,
    target_tier: str,
) -> Dict[str, Any]:
    """Resolve link settings from defaults, calibration, and overrides."""

    baseline = _with_network_calibration(defaults, selected_profile, source_tier, target_tier)
    baseline = _with_energy_link_profile(baseline, energy_profile, source_tier, target_tier)
    return _with_defaults(raw_override, baseline)


def _with_network_calibration(
    base: Dict[str, Any],
    selected_profile: Dict[str, Any] | None,
    source_tier: str,
    target_tier: str,
) -> Dict[str, Any]:
    """Apply a selected network calibration profile when tiers match."""

    if selected_profile is None:
        return base

    selected_pair = {
        str(selected_profile.get("source_tier", "")).lower(),
        str(selected_profile.get("target_tier", "")).lower(),
    }
    expected_pair = {source_tier.lower(), target_tier.lower()}
    if selected_pair != expected_pair:
        return base

    calibrated = dict(base)
    calibrated["latency"] = float(selected_profile["latency_ms"]["median"])
    calibrated["bandwidth"] = float(selected_profile["bandwidth_mbps"]["sustained"])
    if "loss_percent" in selected_profile:
        calibrated["loss_percent"] = float(selected_profile["loss_percent"])
    return calibrated


def _with_energy_node_profile(
    base: Dict[str, Any],
    energy_profile: Dict[str, Any] | None,
    tier: str,
) -> Dict[str, Any]:
    """Apply calibrated node energy coefficients for one infrastructure tier."""

    if energy_profile is None:
        return dict(base)

    node_tiers = dict(energy_profile.get("node_tiers", {}))
    tier_profile = node_tiers.get(tier.lower())
    if tier_profile is None:
        return dict(base)
    return _with_defaults(dict(base), dict(tier_profile))


def _with_energy_link_profile(
    base: Dict[str, Any],
    energy_profile: Dict[str, Any] | None,
    source_tier: str,
    target_tier: str,
) -> Dict[str, Any]:
    """Apply calibrated network transport energy for one tier pair."""

    if energy_profile is None:
        return dict(base)

    link_tiers = dict(energy_profile.get("link_tiers", {}))
    tier_key = f"{source_tier.lower()}_{target_tier.lower()}"
    reverse_key = f"{target_tier.lower()}_{source_tier.lower()}"
    tier_profile = link_tiers.get(tier_key, link_tiers.get(reverse_key))
    if tier_profile is None:
        return dict(base)
    return _with_defaults(dict(base), dict(tier_profile))


def _read_count(
    config: Dict[str, Any],
    name: str,
    *,
    default: int,
    min_value: int,
) -> int:
    """Read an integer node count and enforce a lower bound."""

    value = int(config.get(name, default))
    if value < min_value:
        qualifier = ">=" if min_value else ">="
        raise ValueError(f"{name} must be {qualifier} {min_value}")
    return value


__all__ = [
    "InfrastructureBundle",
    "build_extended_continuum",
]
