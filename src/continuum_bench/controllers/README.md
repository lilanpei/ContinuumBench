# Controllers

This directory contains the baseline control policies used by the benchmark.
Controllers build Eclypse `Application` projections and `PlacementView` objects
directly, then call Eclypse-native strategies where available. ContinuumBench
controller code should only add benchmark-specific scaling, policy selection,
rule-based policy baselines, or metric-facing behavior.

Autoscaling is intentionally modeled as a ContinuumBench benchmark replica
projection over an Eclypse application. Eclypse remains authoritative for
service graphs, substrate resources, paths, and placement audits; ContinuumBench
only toggles which predeclared worker services are active so HPA/KEDA-style and
rule-based policy baselines can be evaluated in a repeatable benchmark loop.

Files:
- `interfaces.py`: shared controller state/action types and worker-pool management helpers
- `placement_only.py`: fixed-placement controller family and extreme baselines
- `separated_loops.py`: joint placement/autoscaling baseline with separate control loops
- `autoscaling_baselines.py`: HPA-style and KEDA-style autoscaling baselines
- `learned_stub.py`: the ContinuumBench paper's **Feature-rule** baseline (a fixed policy over normalized features; no learning)
- `rl_meta.py`: the ContinuumBench paper's **Meta-rule** baseline (deterministic scale/replan/strategy rules; named `rl_meta` only for config/CSV compatibility, no learning)
- `anticipatory.py`: autoscaler that pre-warms pools before known windows of load (version 1.1; used by the intent-compiler paper)
- `constant.py`: reference controller that holds every pool, or each named pool, at a fixed size (version 1.1)
- `extensions.py`: optional hook for loading a controller of your own without editing the runner

Current controller families:
- placement-only baselines
- extreme baselines such as `all_local`, `all_edge`, `all_cloud`, and `no_scaling`
- Eclypse-native placement baselines such as `best_fit`
- stronger ContinuumBench heuristic extensions routed through the placement strategies package
- HPA/KEDA-style autoscaling baselines that use benchmark queue signals and Eclypse-audited replanning
- `learned_stub`, which observes `ControllerState`, emits normal `ControllerAction` objects, and leaves reward logging to the runner
- `rl_meta`, which exposes a small policy action space: scale down/hold/up, replan or hold placement, and choose an Eclypse-audited placement strategy
- `anticipatory` and `placement_only:milp`, whose parameters an intent compiler sets through the knob interface (`continuum_bench.theta`), and `constant`, a fixed-allocation reference

Important distinction:
- `placement_only.py` is mostly about where stages go
- `separated_loops.py` is about how worker counts and placement adapt over time

Paper role:
- this directory defines the comparison ladder from trivial baselines to stronger heuristics
- these policies are the benchmark's reference baselines; they are what a new controller is measured
  against, not an attempt at a state-of-the-art controller

## Adding a controller

1. Define your policy so it consumes a `ControllerState` and returns a `ControllerAction` (scale +
   placement), following the shape of an existing baseline. Reuse the placement strategies in
   `placement/strategies.py` rather than re-implementing placement.
2. Make the runner able to build it, either way:
   - **Without editing the benchmark** (recommended for your own work): add a module
     `continuum_bench/controllers/extensions_local.py` exposing
     `build(name, config, runner)`. Return your controller when `name` matches the
     `--controller` value you want, in lower case (the runner lower-cases it), and `None` otherwise. `extensions.py` loads it on demand, so
     the benchmark stays upgradable underneath you. The `runner` argument lets a composite policy
     build sub-controllers via `runner._build_controller(name, config)`.
   - **As a built-in**: add a `--controller` name branch in
     `ContinuumBenchRunner._build_controller` (`src/continuum_bench/runner.py`), mirroring the
     existing cases, and extend the `--controller` help string in `src/continuum_bench/cli.py`.
3. Add coverage: a smoke run in `tests/test_cli_smoke.py` plus a behavior assertion in the relevant
   controller test (e.g. `tests/test_worker_pool_scaling.py`).
4. Add the controller to a sweep manifest under `sweeps/` to include it in a comparison.
