# Scenarios

This directory defines benchmark workload families.

Current scenarios:
- `eo_dual_stream.py`: Earth-observation workflow with intermittent contact windows and fast vs bulk paths.
- `driving_v2x.py`: V2X/driving workflow with latency-sensitive processing and offload-sensitive execution.
- `split_inference.py`: split-inference workflow with a direct result path and optional refinement.
- `industrial_iot.py`: industrial IoT workflow with a control path and a bulk/archive-style path.

What a scenario provides:
- an application DAG
- anchor/layer semantics for stages
- worker pool definitions
- service profiles used by the runner and placement logic
- infrastructure/topology assumptions needed by the workload family

Benchmark role:
- scenario diversity is the main defense against overfitting benchmark conclusions to one workload shape
- each scenario should stress a different continuum-management tradeoff

When adding a scenario:
- keep it compatible with the current DAG + worker-pool + single-sink runner model unless you intend to evolve that contract explicitly
- add a public config in `configs/`
- add focused tests in `tests/test_scenarios.py` and `tests/test_cli_smoke.py`
- register it either as a branch in `build_scenario` (`__init__.py`), or -- to keep your work
  outside the benchmark -- as a module `continuum_bench/scenarios/extensions_local.py` exposing
  `build(normalized_name, config, seed)`, which `extensions.py` loads on demand
