# Changelog

## 1.1.1 (2026-11-15)

Version 1.1.1 is on `main`. It brings the scenario configs, the sweeps and the analysis of the ContinuumBench paper in line with version 1.0.1.

### Changed

- In the eight scenario configs the edge nodes are linked directly over a LAN (1 ms, 10 Gbps), as the ContinuumBench paper describes, and the Driving V2X configs have no space node (six nodes, 0/3/2/1, as the paper's Table 1 gives). This changes the results of some runs of these configs. The configs of the scenario variants of version 1.1 are unchanged.

### Added

- The sweeps of the ContinuumBench paper's Findings 1 to 3 (`sweeps/replan_ablation.yaml`, `sweeps/replan_milp.yaml`, `sweeps/load_sweep.yaml`, `sweeps/placement_sweep.yaml`) and the control of the placement-stress variant, `configs/eo_placement_control.yaml`.
- `analysis/findings.py`, which prints the numbers of each finding and of the stressor sweeps from their run directories, and stops when a sweep has a missing or repeated run.
- `provenance/sweep.json` in each run directory of a sweep: the manifest run and the seed that produced it.

### Changed in the tools

- `make reproduce` runs the ContinuumBench paper's sweeps, aggregates the main and stressor sweeps and prints every number with `analysis/findings.py`. The figures come from `make extra-sweeps` and `make figures`; `make analysis` no longer runs `analysis/significance.py`.
- `analysis/score_sensitivity.py` writes its summary beside `--grouped-csv` by default and prints its result; `analysis/tables.py` warns about cells that complete no work.
- An unknown tier name under `infrastructure.intra_tier` raises an error.

### Fixed

- A task sent on a hop that has no store-and-forward buffer counts as lost, with the node-failure losses, and as an SLO violation, which can change the results of runs where this happens. Before, it disappeared from every count.
- The Python 3.12 and 3.13 classifiers in the package metadata.

## 1.1.0 (2026-11-13)

Version 1.1.0 is on `main`. It adds what the intent-compiler paper (RISE-DL 2026) needs: three scenario variants, the controllers they use, and a knob interface through which an intent compiler sets the controller's parameters. It also changes some simulator behavior and accounting, which can alter the results of runs that version 1.0 supports, so the ContinuumBench paper's results come from the branch `europar-frame-2026` and the tag `v1.0.1`.

### Added

- The knob interface `continuum_bench.theta`, with its schema `theta_schema.yaml` shipped in the package: nineteen discrete knobs, their validation, and their projection onto the configuration blocks that the controllers read. A run configuration with a `theta:` block is projected once, when the runner is built. The objective weights are recorded in `score.weights` for an objective computed outside the simulator.
- Per-class sink deadlines (`deadline_by_priority`).
- Tier-priced provisioning cost: `cost.price_per_tier` in the configuration; `cost.worker_seconds_by_tier` and `cost.total_tier_priced_worker_seconds` in the run summary.
- The on-time rate of each task class (`qos.on_time_rate_by_priority`) in the run summary, and each node's tier in the run metadata.
- A configurable MILP placement policy (`placement.milp`): objective weights with a cost term weighted by tier, a residency constraint, an egress budget and a cap on dynamic power.
- The controller `anticipatory`, an autoscaler that pre-warms pools before known windows of load, and the reference controller `constant[:N]`, which holds every pool, or each named pool, at a fixed size.
- An optional second scalable pool in `eo_dual_stream` (`scaling.pools.Fusion`) and optional direct links between nodes of the same tier (`infrastructure.intra_tier`, also in version 1.0.1).
- The scenario variants `configs/eo_elastic_bottleneck.yaml`, `configs/eo_placement_stress.yaml` and `configs/eo_two_pool.yaml`.

### Changed

These changes can alter the results of runs that version 1.0 also supports. Version 1.0.1 has the first three.

- Work that a run destroys (buffer overflow, eviction, retry exhaustion, TTL expiry, or loss in transit to a failed node) now counts as undelivered work in the deadline accounting: a discarded task counts as missed instead of dropping out of the on-time rate.
- A message re-sent from a store-and-forward buffer takes a size-aware transfer time, as a fresh send does.
- A message larger than a link's per-epoch budget is admitted alone on an idle link, and a buffer releases such an item at its head; before, it stalled for the rest of the run.
- The MILP planner places unanchored stages on edge and cloud nodes only, wherever it is used. The placement-only MILP controller also falls back to the minimum-latency heuristic when its mapping over-subscribes a node at run time.
- With `placement.replan_on_scale: false`, an autoscaler whose new workers do not fit around the frozen placement re-solves the whole placement instead of deferring the scale-up.
- The MILP energy objective counts dynamic power only, since the simulator keeps every node powered whatever the placement.

### Fixed

- The author's name in `LICENSE` and the package metadata: Lanpei Li.

## 1.0.1 (2026-11-15)

The version of the ContinuumBench paper's results (FRAME 2026 at Euro-Par 2026), on the branch `europar-frame-2026` and the tag `v1.0.1`. It adds to version 1.0.0 the simulator fixes, the edge-tier LAN and the Driving V2X node count with which the paper's results were produced, and the sweeps and analysis of the paper's findings; that branch's changelog lists them.

## 1.0.0

The first release of the ContinuumBench paper's code.
