# Metrics

## Purpose

This directory owns the per-run artifacts and summary KPIs emitted by the benchmark.

## Key Files

- `logger.py`: writes `metadata.json`, `epochs.jsonl`, `decision_trajectory.jsonl`, `epochs.csv`, and `summary.json` for each run.
- `summary.py`: reduces per-epoch records into aggregate latency, QoS, traffic, resource, energy, reliability, freshness, stability, controller reward, and controller-overhead metrics.
- `__init__.py`: package exports used by the runner.

Resource records distinguish Eclypse placement reservations from benchmark runtime activity:
- `resources.reserved` is derived from Eclypse placement-view reservations.
- `resources.active_runtime` is derived from ready queued work in the benchmark loop.
- top-level `resources.*` fields remain as backward-compatible aliases for active runtime demand.

Energy and reliability summaries are benchmark metrics. The power, battery, and link-energy coefficients are declared on Eclypse graph assets, while ContinuumBench integrates them over epochs and aggregates the resulting KPIs.

Task accounting separates:
- `completed_tasks`: tasks delivered to the sink.
- `failed_tasks`: buffer drop/expiry plus failure-event task loss.
- `pending_tasks`: generated tasks still unresolved at the horizon.
- `resolved_tasks`: generated tasks that either completed or failed, capped at generated work.

Paper analysis requires these fields. Old summaries that lack them should be rerun, not backfilled.

## When To Read This

- when you are changing run artifact formats or adding a new benchmark KPI
- when a test failure points to summary computation or per-epoch logging
