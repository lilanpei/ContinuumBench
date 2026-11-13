# Oracles

This directory contains offline oracle support for reduced benchmark instances.

Current implementation:
- `milp_oracle.py`

Important note:
- the default public surface is an **exact small-instance oracle** implemented by deterministic exhaustive enumeration
- an optional SciPy/HiGHS MILP backend is available for linear reduced-snapshot objectives

Primary API:
- `solve_small_instance(snapshot, objective="latency", backend="auto")`

Supported objectives:
- `"latency"`: backward-compatible latency-only objective
- `{"weights": {...}}`: weighted exact objective over reduced-snapshot components:
  - `latency`
  - `energy` (dynamic power only: idle power does not depend on the placement)
  - `cost` (per-node `price` times CPU demand, scaled by `cost_scale`; version 1.1)
  - `resource_overcommit`
  - `failure_risk`
- callable objective hooks for experiment-specific `J(snapshot, placement)` definitions

Backend scope:
- `backend="enumeration"` evaluates all feasible assignments and supports all objectives above
- `backend="milp"` requires the optional `milp` dependency and currently supports latency, energy and cost objectives
- `backend="auto"` tries the MILP backend for supported objectives and falls back to enumeration when the solver is unavailable or the objective is not linear in the current reduced model

Hard constraints (version 1.1): a snapshot may carry a `constraints` block with `egress_budget_mb` and `energy_cap_w`; both backends enforce it.

Current result surface:
- placement
- objective value
- solver status
- optimality gap
- method/backend metadata

Benchmark role:
- small-instance optimal-reference baseline
- gap-to-optimal measurement
- solver-backed static placement reference for reduced placement snapshots
- future source of expert labels for imitation/offline learning

Future direction:
- extend the MILP model to cover additional objective components and larger reduced snapshots while keeping the same oracle interface
