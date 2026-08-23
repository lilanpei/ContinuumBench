from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

from continuum_bench._eclypse import ensure_eclypse_on_path
from continuum_bench.scenarios.extensions import build_extension_scenario


@dataclass(frozen=True)
class RouteSpec:
    mode: str
    target: str = ""
    high_target: str = ""
    low_pool: str = ""
    balance_pool: str = ""   # mode == "balance": round-robin ALL tasks across this pool's ready replicas


@dataclass
class ScenarioBuild:
    name: str
    application: Any
    infrastructure: Any
    contact_edges: List[Tuple[str, str]]
    stage_profiles: Dict[str, Dict[str, Any]]
    source_stages: List[str]
    sink_stage: str
    route_specs: Dict[str, RouteSpec]
    worker_pools: Dict[str, List[str]]
    source_stream_map: Dict[str, str] = field(default_factory=dict)


def _build_application(application_id: str) -> Any:
    """Build an Eclypse application graph for one benchmark scenario."""

    ensure_eclypse_on_path()
    try:
        from eclypse.graph import Application
        from eclypse.graph.assets import Symbolic
        from eclypse.graph.assets.defaults import cpu, ram, storage
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Eclypse is required for ContinuumBench application graphs. "
            "Install eclypse or set ECLYPSE_HOME to a local checkout."
        ) from exc

    return Application(
        application_id=application_id,
        include_default_assets=False,
        node_assets={
            "cpu": cpu(init_fn_or_value=0.0),
            "ram": ram(init_fn_or_value=0.0),
            "storage": storage(init_fn_or_value=0.0),
            "placement_layers": Symbolic(
                lower_bound=[],
                upper_bound=["space", "iot", "edge", "cloud"],
                init_fn_or_value=[],
            ),
        },
    )


def _with_runtime_aliases(profile: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize profile keys expected by the runtime."""

    resolved = copy.deepcopy(profile)

    if "processing_time" not in resolved and "processing_time_s" in resolved:
        resolved["processing_time"] = resolved["processing_time_s"]
    if "processing_time_s" not in resolved and "processing_time" in resolved:
        resolved["processing_time_s"] = resolved["processing_time"]

    if "data_size" not in resolved and "output_size_bytes" in resolved:
        resolved["data_size"] = resolved["output_size_bytes"]
    if "output_size_bytes" not in resolved and "data_size" in resolved:
        resolved["output_size_bytes"] = resolved["data_size"]

    if "deadline" not in resolved and "deadline_s" in resolved:
        resolved["deadline"] = resolved["deadline_s"]
    if "deadline_s" not in resolved and "deadline" in resolved:
        resolved["deadline_s"] = resolved["deadline"]

    return resolved


def _profile_assets(stage_name: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Build graph assets for one stage profile."""

    assets = copy.deepcopy({key: value for key, value in profile.items() if key != "resources"})
    assets.update(copy.deepcopy(profile.get("resources", {})))
    assets["stage_name"] = stage_name
    return assets


def _require_infrastructure_layers(
    scenario_name: str,
    infrastructure: Any,
    required_layers: Dict[str, int],
) -> None:
    """Validate that required anchored layers exist."""

    for layer, minimum in required_layers.items():
        count = _count_nodes_in_layer(infrastructure, layer)
        if count < minimum:
            raise ValueError(
                f"{scenario_name} requires at least {minimum} {layer} node(s) for anchored stages"
            )


def _required_layers_from_stage_profiles(
    stage_profiles: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    """Read required layers from Eclypse symbolic placement assets."""

    required_layers: Dict[str, int] = {}
    for profile in stage_profiles.values():
        layer = _placement_layer_from_profile(profile)
        if layer is not None:
            required_layers[layer] = 1
    return required_layers


def _placement_layer_from_profile(profile: Dict[str, Any]) -> str | None:
    """Return the single Eclypse symbolic placement layer declared by a profile."""

    resources = dict(profile.get("resources", {}))
    placement_layers = resources.get("placement_layers", profile.get("placement_layers"))
    if isinstance(placement_layers, list) and len(placement_layers) == 1:
        layer = str(placement_layers[0]).lower()
        if layer in {"space", "iot", "edge", "cloud"}:
            return layer

    return None


def _count_nodes_in_layer(infrastructure: Any, layer: str) -> int:
    """Count infrastructure nodes assigned to one layer."""

    nodes = getattr(infrastructure, "nodes", {})
    if hasattr(nodes, "items"):
        items = list(nodes.items())
    else:
        items = [(node_id, nodes[node_id]) for node_id in nodes]

    return sum(
        1
        for _node_id, attrs in items
        if _node_matches_layer(dict(attrs), layer)
    )


def _node_matches_layer(attrs: Dict[str, Any], layer: str) -> bool:
    """Return whether one node asset record declares a placement layer."""

    placement_layers = attrs.get("placement_layers", [])
    if isinstance(placement_layers, list) and len(placement_layers) == 1:
        if str(placement_layers[0]).lower() == layer:
            return True
    return False


def build_scenario(name: str, config: Dict[str, Any], seed: int) -> ScenarioBuild:
    """Dispatch scenario construction by normalized name."""

    normalized = name.lower().replace("-", "_")

    if normalized in {"eo_dual_stream", "eo"}:
        from continuum_bench.scenarios.eo_dual_stream import build

        return build(config=config, seed=seed)

    if normalized in {"driving_v2x", "driving", "v2x"}:
        from continuum_bench.scenarios.driving_v2x import build

        return build(config=config, seed=seed)

    if normalized in {"split_inference", "split"}:
        from continuum_bench.scenarios.split_inference import build

        return build(config=config, seed=seed)

    if normalized in {"industrial_iot", "industrial", "iiot"}:
        from continuum_bench.scenarios.industrial_iot import build

        return build(config=config, seed=seed)

    extension = build_extension_scenario(normalized, config, seed)
    if extension is not None:
        return extension

    raise ValueError(f"Unsupported scenario: {name}")


__all__ = ["RouteSpec", "ScenarioBuild", "build_scenario"]
