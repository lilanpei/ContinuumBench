# Tests

## Purpose

This directory contains the benchmark regression suite.

## Key Files

- `test_runner_integration.py`: end-to-end runner semantics and cross-component behavior.
- `test_scenarios.py`: scenario construction and scenario-specific contracts.
- `test_sweeps.py`: sweep manifest parsing and batch execution plumbing.
- `test_analysis_pipeline.py`: offline aggregation and plotting checks.
- `test_readme_contents.py` and `test_repo_layout.py`: repository-readability and layout guardrails.

## When To Read This

- when you are changing benchmark behavior and need to place or update regression coverage
- when you need to understand which tests protect a specific subsystem

Organization by concern:
- `test_runner_integration.py`: end-to-end runner semantics and cross-component behavior
- `test_scenarios.py`: scenario construction and scenario-specific contracts
- `test_worker_pool_scaling.py`: worker-pool behavior and controller interactions
- `test_placement_strategies.py`: placement heuristic semantics and distinctness
- `test_workload_and_summary.py`: workload generation and aggregate summary metrics
- `test_sweeps.py`: sweep manifest parsing and batch execution plumbing
- `test_analysis_pipeline.py`: offline aggregation and plotting pipeline
- `test_ai_readiness.py`: policy-trajectory logging and split manifests
- `test_calibration_profiles.py`: calibration profile schema, provenance, selection, and trace-backed config behavior
- `test_milp_oracle.py`: exact small-instance oracle behavior
- smaller smoke tests cover CLI, package layout, README assertions, and buffer/runtime primitives

Design intent:
- keep most tests small and focused
- protect benchmark-paper semantics directly in regression tests, including protocol naming, calibration selection, and trace-backed behavior
- use real Eclypse `Application`, `Infrastructure`, `PlacementView`, and native strategies in substrate-facing tests
- when testing paths or links, declare Eclypse edge assets and path aggregators instead of relying on raw edge dictionaries
- make artifact plumbing testable without rerunning large benchmark sweeps in every test

Recommended workflow:
- use focused tests while implementing
- run `pytest tests -q` before committing a change
