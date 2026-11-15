# Analysis Pipeline

> **Reproducing the ContinuumBench paper:** [`../REPRODUCE.md`](../REPRODUCE.md) maps each table
> and finding to its sweep and gives the values to expect; `make reproduce` runs the sweeps,
> aggregates them and prints the numbers with `findings.py`.

## Purpose

This directory contains the file-based aggregation and plotting steps used to turn completed ContinuumBench sweep runs into analysis artifacts.

## Key Files

- `aggregate_runs.py`: loads completed run directories and writes the tidy aggregate outputs used by tables and plots.
- `tables.py`: builds grouped and completion-aware ranking CSVs from `aggregate.csv`.
- `figures.py`: renders the publication figures from grouped analysis CSVs.
- `score_sensitivity.py`: perturbs completion-aware score weights and reports whether the family-level comparison changes.
- `findings.py`: prints the numbers of the paper's Table 3, its Findings 1 to 4 and the stressor rank changes, one subcommand per result, from the run directories of a sweep.
- `significance.py`: paired Wilcoxon tests of the main comparison (not reported in the paper).
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

## The Paper's Numbers

[`../sweeps/README.md`](../sweeps/README.md) lists the sweeps. After running them, aggregate the
main comparison, then print each result:

```bash
python analysis/aggregate_runs.py --sweep-dir runs/main --out runs/main/analysis
python analysis/tables.py --aggregate-csv runs/main/analysis/aggregate.csv --out runs/main/analysis
python analysis/score_sensitivity.py --grouped-csv runs/main/analysis/grouped_strict.csv
python analysis/findings.py main --sweep-dir runs/main
python analysis/findings.py replan --sweep-dir runs/replan_ablation
```

`findings.py` reads each run's `summary.json` and the `provenance/` files that the sweep runner
stores beside it, so it needs no aggregate. Its other subcommands are `replan-milp`, `load`,
`placement`, `failure` and `stressors` (`make findings` runs them all). Interpret `main` as
controller-comparison evidence and the regime, overlay and sensitivity sweeps as benchmark-validity
evidence.

Current publication sweeps use a longer completion-aware horizon: `epochs` is the source-arrival window and `drain_epochs` keeps the runtime active without generating new work. The paper's manifests use `epochs: 120` and `drain_epochs: 60`, except the placement-stress variant and its control, which keep their own `60 + 30`; the SOTA pilot uses `80 + 40` for a faster sanity check. These manifests repeat finite piecewise workload regimes, and repeat contact traces when a scenario has an intermittent workflow boundary, so longer contact-aware runs do not fall into a permanent post-trace no-contact state.

When protocol settings change, delete the old run directory and rerun. The analysis code intentionally does not preserve compatibility with old erroneous run artifacts.
