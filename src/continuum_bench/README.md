# ContinuumBench Package

This package contains the benchmark runtime, benchmark scenarios, controller baselines, sweep support, calibration-aware builders, and offline oracle support.

ContinuumBench uses Eclypse as the substrate layer. Application and infrastructure graphs, node/link assets, residual placement views, structural paths, route-cost timing, and graph update-policy mutation should come from Eclypse first. The package code should stay focused on benchmark-specific workload dynamics, temporary contact reachability, controller policies, metric/reward logging, calibration selection, and analysis outputs.

Main entry points:
- `cli.py`: command-line interface for single runs, summaries, and sweeps
- `runner.py`: deterministic epoch-driven benchmark loop over Eclypse graphs and placements
- `theta.py` and `theta_schema.yaml`: the knob interface through which an intent compiler sets the controller's parameters (version 1.1)

Key subpackages:
- `controllers/`: controller baselines, scaling interfaces, and adaptive-controller interfaces
- `scenarios/`: workload/scenario builders
- `placement/`: Eclypse-native placement adapter plus benchmark-only strategy extensions
- `oracles/`: exact small-instance oracle support
- `calibration/`: package-owned calibration assets used by opt-in calibrated and trace-backed configs
- `metrics/`: epoch-level logging and summary generation
- `sweeps/`: sweep manifest parsing and batch execution
- `runtime/`: store-and-forward and service helpers for benchmark workload dynamics
- `infra/`: Eclypse infrastructure construction and update-policy adapters
- `workload/`: stochastic workload generators

Design intent:
- benchmark first
- reproducible experiment execution
- Eclypse-native substrate behavior before local extensions
- reusable for controller-training studies through trajectory logging and split manifests
- explicit evaluation-regime comparison through the publication sweep family

If you are new to the codebase, read in this order:
- `controllers/`: controller state/action contracts, placement-only baselines, and the joint scaling baseline.
- `scenarios/`: scenario builders that define the DAGs, worker pools, and infrastructure assumptions.
- `calibration/`: package-owned calibration YAML assets selected by opt-in configs.
- `infra/`: topology generation and intermittent-contact helpers used by the runner.
- `metrics/`: epoch-level logging and end-of-run summary generation.
- `runtime/`: store-and-forward buffering and generic runtime skeleton helpers.
- `sweeps/`: manifest parsing and batch execution support for batch studies.
- `workload/`: deterministic workload generators used by scenario sources.

For the main execution path, start with `cli.py` and `runner.py`, then use the directory READMEs above when you need subsystem-level detail.
