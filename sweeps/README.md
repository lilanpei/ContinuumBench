# Sweeps

## Purpose

This directory contains benchmark sweep manifests.

## Key Files

- `sota_pilot.yaml`: small SOTA-comparable pilot matrix used to check baseline coverage and metric coverage.
- `main.yaml`: larger publication matrix over scenarios, F0/F2 endpoints, controller families including `rl_meta` and a solver-backed MILP placement reference, and 10 seeds.
- `realism_ladder.yaml`: legacy-named evaluation-regime and overlay ablation using the old T0--T4 compatibility labels.
- `sensitivity_load.yaml`: load-sensitivity protocol over F2 configs.
- `sensitivity_failure.yaml`: node-failure-sensitivity protocol over base configs.
- `delay_breakdown.yaml`: compact F2 sweep for per-cause completed-task delay components.
- `milp_reference.yaml`: compact F2 solver-backed static placement reference.

## When To Read This

- when you need to run or extend a batch experiment
- when you are deciding whether a change belongs in a single config or in a manifest-defined comparison set

Current public sweep manifests:
- `main.yaml`: full baseline-family publication matrix for main runs
- `realism_ladder.yaml`: evaluation-regime and overlay ablation for ranking-sensitivity analysis
- `sensitivity_load.yaml`: load stress sweep for dynamics validation
- `sensitivity_failure.yaml`: failure stress sweep for dynamics validation
- `delay_breakdown.yaml`: delay-cause sensitivity sweep
- `milp_reference.yaml`: MILP placement-reference sweep

What a sweep manifest does:
- selects one base config
- optionally applies top-level overrides shared by every run, such as horizon length and repeat flags
- selects a list of seeds
- defines multiple named runs with controller choices and config overrides

Run a sweep with:

```bash
python -m continuum_bench.cli sweep --manifest sweeps/main.yaml --out runs/main
```

The sweep runner expands each manifest deterministically and writes one run directory per `(run, seed)` pair.

Artifact behavior:
- recorded per-run configs are preserved as immutable provenance inside each run directory
- nested/versioned sweep layouts are supported by the manifest path resolver

When adding new sweep manifests:
- keep names study-specific and descriptive
- prefer a small number of explicit runs over a huge opaque manifest
- document the intended comparison set in the manifest comments or in `analysis/README.md`

Publication use:
- use `sota_pilot.yaml` to validate a proposed controller matrix, including the rule-based meta-policy controller, quickly
- use `main.yaml` for the main controller comparisons
- use `realism_ladder.yaml` and sensitivity manifests for evaluation-regime, overlay, and dynamics analysis
- use `delay_breakdown.yaml` and `milp_reference.yaml` for focused supporting evidence rather than controller ranking tables
- aggregate with `analysis/aggregate_runs.py`, then build completion-aware ranking tables with `analysis/tables.py`

Current publication horizon policy:
- `main.yaml`, `realism_ladder.yaml`, `sensitivity_load.yaml`, and `sensitivity_failure.yaml` use `epochs: 120` plus `drain_epochs: 60`.
- `sota_pilot.yaml` uses `epochs: 80` plus `drain_epochs: 40` to keep pilot runs shorter.
- the top-level `workload.repeat: true` repeats finite piecewise arrival regimes for the longer horizon.
- the top-level `infrastructure.contact_schedule.repeat: true` repeats finite trace windows only when an expanded run already has a trace contact schedule.
- old run artifacts from earlier horizon/contact settings should be deleted and regenerated.
