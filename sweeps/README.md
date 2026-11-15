# Sweeps

## Purpose

This directory contains the sweep manifests of the ContinuumBench paper. [REPRODUCE.md](../REPRODUCE.md) maps each table and finding of the paper to its manifest.

## Key Files

The paper's sweeps:
- `main.yaml`: the main comparison, nine controllers on four scenarios under R1 and R2, ten seeds (720 runs; Table 3 and the score-robustness check).
- `sensitivity_load.yaml`, `sensitivity_contact.yaml`, `sensitivity_failure.yaml`: one stressor at a time (arrival load, contact severity, node unavailability) for six controllers, three seeds (216, 108 and 216 runs; the stressor rank changes and Finding 4).
- `replan_ablation.yaml`: three placement schedules over 24 configurations that vary the node count (each scenario's own and synthesized infrastructures of 22 to 48 nodes), the placement strategy, the controller and the regime, five seeds (360 runs; Finding 1).
- `replan_milp.yaml`: the MILP re-planner that relocates workers, on EO with twice the load and 8-core edge nodes, with and without re-planning, 15 seeds (30 runs; Finding 1).
- `load_sweep.yaml`: arrival rates from 0.6x to 2.0x under R1 for six controllers, including the over-provisioned reference, five seeds (480 runs; Finding 2).
- `placement_sweep.yaml`: six placement strategies at fixed capacity on the placement-stress variant, its control and unmodified EO, five seeds (90 runs; Finding 3).

Further sweeps, not reported in the paper:
- `sota_pilot.yaml`: a small pilot matrix for checking a controller set end to end (`make smoke`).
- `realism_ladder.yaml`: the evaluation regimes and overlays step by step, for ranking-sensitivity analysis.
- `delay_breakdown.yaml`: a compact F2 sweep for per-cause delay components of completed tasks.
- `milp_reference.yaml`: a compact F2 sweep of the MILP placement reference.

## When To Read This

- when you need to run or extend a batch experiment
- when you are deciding whether a change belongs in a single config or in a manifest-defined comparison set

What a sweep manifest does:
- selects one base config, which a run can replace with its own `config`
- optionally applies top-level overrides shared by every run, such as horizon length and repeat flags
- selects a list of seeds
- defines multiple named runs with controller choices and config overrides

Run a sweep with:

```bash
python -m continuum_bench.cli sweep --manifest sweeps/main.yaml --out runs/main
```

The sweep runner expands each manifest deterministically and writes one run directory per `(run, seed)` pair. Each run directory keeps, under `provenance/`, the rendered config (`config.yaml`) and the manifest run and seed that produced it (`sweep.json`); the analysis scripts read them to tell apart runs that share a scenario and a controller.

Horizon policy:
- The paper's sweeps use `epochs: 120` plus `drain_epochs: 60`, except the placement-stress variant and its control, which keep their own horizon of 60 plus 30 epochs.
- `sota_pilot.yaml` uses `epochs: 80` plus `drain_epochs: 40` to keep pilot runs shorter.
- The top-level `workload.repeat: true` repeats finite piecewise arrival regimes for the longer horizon.
- The top-level `infrastructure.contact_schedule.repeat: true` repeats finite trace windows only when an expanded run already has a trace contact schedule.

When adding new sweep manifests:
- keep names study-specific and descriptive
- prefer a small number of explicit runs over a huge opaque manifest
- say in a comment at the top of the manifest what it compares and how to analyze it
