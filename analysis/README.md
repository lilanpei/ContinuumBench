# Analysis Pipeline

> **Reproducing the submitted paper:** use `sweeps/main.yaml` (main comparison) plus the
> `realism_ladder`, `sensitivity_*`, `delay_breakdown`, and `milp_reference`
> manifests, then `aggregate_runs.py` -> `tables.py` -> `figures.py`. See
> [`../REPRODUCE.md`](../REPRODUCE.md) for the artifact-to-command map.

## Purpose

This directory contains the file-based aggregation and plotting steps used to turn completed ContinuumBench sweep runs into analysis artifacts.

## Key Files

- `aggregate_runs.py`: loads completed run directories and writes the tidy aggregate outputs used by tables and plots.
- `tables.py`: builds grouped and completion-aware ranking CSVs from `aggregate.csv`.
- `figures.py`: renders the publication figures from grouped analysis CSVs.
- `score_sensitivity.py`: perturbs completion-aware score weights and reports whether the family-level comparison changes.
- `README.md`: documents the expected inputs, outputs, and offline analysis workflow.

## When To Read This

- when you have completed a sweep and need aggregate CSV, grouped summaries, or plots
- when you are changing the experiment protocol or analysis workflow

## Inputs

The aggregation step expects a completed sweep directory (for example `runs/main/`), with one subdirectory per `(manifest run, seed)` pair, each containing a `summary.json` file.

Example layout:

```text
runs/main/
  20260406_..._eo_dual_stream_separated_loops_seed0/
    summary.json
  20260406_..._split_inference_round_robin_seed0/
    summary.json
  20260406_..._industrial_iot_no_scaling_seed0/
    summary.json
```

The pipeline is offline and deterministic. It only reads the saved run summaries; it does not rerun benchmarks.

## Aggregate A Sweep

```bash
python analysis/aggregate_runs.py \
  --sweep-dir runs/main \
  --out runs/main/analysis
```

Outputs:

- `aggregate.csv`: one tidy row per run with publication metrics, including latency, throughput, resource utilization, energy, reliability, SLO, buffering, and stability fields. New runs must include `pending_tasks`, `pending_rate`, and `resolved_rate`; old summaries are rejected rather than backfilled.
- `grouped_summary.json`: grouped mean/min/max statistics by `scenario` and `controller` for table generation.
- Aggregation is strict for publication tables and raises if required `summary.json` sections or fields are missing.

## Publication Tables

After aggregating a sweep, build strict publication tables with:

```bash
python analysis/tables.py \
  --aggregate-csv runs/main/analysis/aggregate.csv \
  --out runs/main/analysis
```

Outputs:

- `grouped_strict.csv`: grouped means/stds by `(scenario, evaluation_regime, stressor overlays, controller)`.
- `rankings.csv`: controller ranks within each scenario, evaluation regime, and overlay setting. Legacy `realism_tier` values remain in the CSV for compatibility.

The ranking table is completion-aware. A controller with very low completion can have an artificially low p95 latency because it delivered little work; `tables.py` marks such variants as invalid for latency-only ranking unless they pass `--min-completion-for-latency-rank`.
The composite completion-aware score also penalizes pending work, so horizon-unfinished tasks remain visible instead of being silently treated as successes or failures.
Use `score_sensitivity.py` to check whether the score-selected controller families remain stable under reasonable weight perturbations before using score-selected summaries.

## Publication Sweep Family

Use the newer sweep family for the current experiment suite:

- `sweeps/sota_pilot.yaml`: 3-seed pilot over the full baseline family, including `rl_meta`.
- `sweeps/main.yaml`: 10-seed main matrix over four scenarios, F0/F2 endpoints, and nine controller variants, including the rule-based meta-policy controller and a solver-backed MILP placement reference.
- `sweeps/realism_ladder.yaml`: legacy-named evaluation-regime and overlay ablation covering the old T0--T4 compatibility labels.
- `sweeps/sensitivity_load.yaml`: arrival-rate sensitivity.
- `sweeps/sensitivity_failure.yaml`: node-failure sensitivity.
- `sweeps/sensitivity_contact.yaml`: contact-trace sensitivity for EO and Driving V2X, the scenarios whose workflows contain explicit intermittent boundaries.
- `sweeps/delay_breakdown.yaml`: per-cause completed-task delay attribution for F2 HPA and joint-control runs.
- `sweeps/milp_reference.yaml`: compact F2 solver-backed static placement reference.

Recommended workflow:

```bash
python -m continuum_bench.cli sweep --manifest sweeps/main.yaml --out runs/main
python analysis/aggregate_runs.py --sweep-dir runs/main --out runs/main/analysis
python analysis/tables.py --aggregate-csv runs/main/analysis/aggregate.csv --out runs/main/analysis
python analysis/score_sensitivity.py --grouped-csv runs/main/analysis/grouped_strict.csv --out runs/main/analysis/score_sensitivity.json
```

Run `realism_ladder` and the sensitivity sweeps the same way. Interpret `main` as controller-comparison evidence and the evaluation-regime, overlay, and sensitivity sweeps as benchmark-validity evidence.

Current publication sweeps use a longer completion-aware horizon: `epochs` is the source-arrival window and `drain_epochs` keeps the runtime active without generating new work. The main, evaluation-regime, and sensitivity manifests use `epochs: 120` and `drain_epochs: 60`; the SOTA pilot uses `80 + 40` for a faster sanity check. These manifests repeat finite piecewise workload regimes, and repeat contact traces when a scenario has an intermittent workflow boundary, so longer contact-aware runs do not fall into a permanent post-trace no-contact state.

When protocol settings change, delete the old run directory and rerun. The analysis code intentionally does not preserve compatibility with old erroneous run artifacts.
