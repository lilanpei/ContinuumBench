"""The knob vector ``theta`` of the tactical controller.

An intent compiler translates an operator's intent into ``theta``, a flat vector of discrete
knobs. The simulator consumes it in three places: the placement MILP, the anticipatory
autoscaler and the runner's per-class deadlines. The objective weights are recorded in the
configuration (``score.weights``) for the intent's declared objective, which is computed outside
the simulator from the run summary. This module owns the knob schema (``theta_schema.yaml``,
shipped with the package), validation, and the projection of ``theta`` onto the configuration
blocks that the controllers read. Controllers never see
``theta`` directly: a run configuration that carries a ``theta:`` block is projected once, when
the runner is constructed (see ``ContinuumBenchRunner.__init__``).
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().with_name("theta_schema.yaml")

# Tier price weights that theta does not set: sources are anchored on these tiers, so their
# price never trades off against another placement.
_FIXED_TIER_PRICES = {"iot": 1.0, "space": 1.0}
# One priced CPU core in the MILP's latency units (ms): with w_cost = 1, a price difference
# of 1 per core on a 5-core stage weighs as much as a 100 ms hop. It is a calibration constant.
MILP_COST_SCALE_MS = 20.0


class ThetaError(ValueError):
    """Raised when a theta is malformed or outside its declared levels (invalid)."""


def load_schema(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the theta schema mapping from YAML."""

    schema_path = Path(path) if path is not None else DEFAULT_SCHEMA_PATH
    with schema_path.open("r", encoding="utf-8") as handle:
        schema = yaml.safe_load(handle)
    if not isinstance(schema, dict) or not isinstance(schema.get("knobs"), dict):
        raise ThetaError(f"theta schema must define a 'knobs' mapping: {schema_path}")
    return schema


def default_theta(schema: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Return theta_def: every knob at its declared default, in canonical key order."""

    knobs = (schema or load_schema())["knobs"]
    return {name: _coerce(spec, spec["default"]) for name, spec in knobs.items()}


def validate_theta(theta: Mapping[str, Any], schema: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Return a complete, canonically ordered theta; missing knobs take their default.

    Unknown keys and values outside the declared levels raise ``ThetaError``: such a theta is
    rejected as invalid, never clamped silently.
    """

    knobs = (schema or load_schema())["knobs"]
    unknown = sorted(set(theta) - set(knobs))
    if unknown:
        raise ThetaError(f"unknown theta knob(s): {unknown}")

    resolved: Dict[str, Any] = {}
    for name, spec in knobs.items():
        raw = theta.get(name, spec["default"])
        try:
            value = _coerce(spec, raw)
        except (TypeError, ValueError) as exc:
            raise ThetaError(f"theta.{name}={raw!r} is not a {spec['type']}") from exc
        levels = [_coerce(spec, level) for level in spec["levels"]]
        if not any(_level_equal(value, level) for level in levels):
            raise ThetaError(f"theta.{name}={raw!r} is not one of the declared levels {spec['levels']}")
        resolved[name] = value
    return resolved


def apply_theta(config: Mapping[str, Any], theta: Mapping[str, Any], schema: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Project a validated theta onto the configuration blocks that the controllers read.

    Returns a deep copy of ``config``; the input is not modified. The projection is the
    interface between the intent compiler and the controller: the objective weights go to
    ``score.weights`` for the declared objective, the per-class deadlines to the runner, the
    scaling policy to the anticipatory autoscaler and the pool limits, and the placement policy,
    tier price weights and hard constraints to the MILP. The projection overwrites the
    configuration's own values in the blocks it sets.
    """

    theta = validate_theta(theta, schema)
    out: Dict[str, Any] = copy.deepcopy(dict(config))

    # Objective weights of the intent's declared objective, which is computed outside the simulator.
    score = _block(out, "score")
    score["weights"] = {
        "cost": theta["w_cost"],
        "energy": theta["w_energy"],
        "latency_p95_s": theta["w_p95"],
        "migrations": theta["w_mig"],
    }

    # Per-class deadlines (runner). "low" is the bulk class routed to the elastic pool.
    out["deadline_by_priority"] = {"high": theta["deadline_high_s"], "low": theta["deadline_low_s"]}

    # Scaling policy (anticipatory controller + pool caps).
    anticipatory = _block(_block(out, "autoscaling"), "anticipatory")
    anticipatory["lead_s"] = theta["prewarm_lead_s"]
    anticipatory["drain_s"] = theta["drain_s"]
    anticipatory["prewarm_workers"] = theta["prewarm_workers"]
    anticipatory["prewarm_by_pool"] = {"Fusion": theta["prewarm_fusion"]}
    anticipatory["target_utilization"] = theta["scale_up_util"]
    cap = int(theta["worker_cap"])
    floors = {"fusion": int(theta["floor_fusion"]), "bulk": int(theta["floor_bulk"])}
    for pool_name, pool_cfg in _block(_block(out, "scaling"), "pools").items():
        # A cap restricts the scenario's pool; it never enlarges it (the scenario builds
        # max_workers replicas, so enlarging would change the application graph).
        pool_cfg["max_workers"] = min(int(pool_cfg.get("max_workers", cap)), cap)
        for key in ("min_workers", "initial_workers"):
            if key in pool_cfg:
                pool_cfg[key] = min(int(pool_cfg[key]), pool_cfg["max_workers"])
        # A floor raises the pool's minimum (the workers held between bursts) up to its cap,
        # never below the scenario's own minimum. A floor does not raise initial_workers: every policy starts cold, as a constant policy also
        # waits out the start-up delay.
        floor = floors["fusion"] if str(pool_name).lower() == "fusion" else floors["bulk"]
        pool_cfg["min_workers"] = max(int(pool_cfg.get("min_workers", 1)), min(floor, pool_cfg["max_workers"]))

    # Placement policy and hard constraints (MILP). The tier prices are weights in the MILP's
    # cost term; the run's tier-priced cost uses the deployment's own cost.price_per_tier.
    tier_prices = {
        "cloud": theta["tier_price_cloud"],
        "edge": theta["tier_price_edge"],
        **_FIXED_TIER_PRICES,
    }
    # The cost scale is a calibration constant; a run may override it through
    # ``theta_options.milp_cost_scale_ms`` without changing the schema.
    cost_scale = float((config.get("theta_options") or {}).get("milp_cost_scale_ms", MILP_COST_SCALE_MS))
    _block(out, "placement")["milp"] = {
        "weights": {"latency": 1.0, "cost": theta["w_cost"], "energy": theta["w_energy"]},
        "cost_scale": cost_scale,
        "tier_prices": tier_prices,
        "residency": theta["residency"],
        "egress_budget_mb": theta["egress_budget_mb"],
        "energy_cap_w": theta["energy_cap_w"],
    }

    out["theta"] = dict(theta)
    return out


def _block(config: Dict[str, Any], key: str) -> Dict[str, Any]:
    """Return ``config[key]`` as a mutable mapping, creating it when absent."""

    block = config.get(key)
    if not isinstance(block, dict):
        block = {}
        config[key] = block
    return block


def _coerce(spec: Mapping[str, Any], value: Any) -> Any:
    """Coerce a raw value to the knob's declared type."""

    kind = str(spec.get("type", "float"))
    if kind == "int":
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise ValueError(value)
        return int(value)
    if kind == "float":
        if isinstance(value, bool):
            raise ValueError(value)
        return float(value)
    if kind == "str":
        return str(value)
    raise ThetaError(f"unsupported knob type: {kind}")


def _level_equal(value: Any, level: Any) -> bool:
    """Compare a value with a declared level, treating infinities exactly."""

    if isinstance(value, float) and isinstance(level, float):
        if math.isinf(value) or math.isinf(level):
            return value == level
        return math.isclose(value, level, rel_tol=0.0, abs_tol=1e-12)
    return value == level


__all__ = [
    "DEFAULT_SCHEMA_PATH",
    "ThetaError",
    "apply_theta",
    "default_theta",
    "load_schema",
    "validate_theta",
]
