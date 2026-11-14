# Changelog

## 1.1.0 (2026-11-13)

Version 1.1.0 is on `main`. It adds what the intent-compiler paper (RISE-DL 2026) needs: three scenario variants, the controllers they use, and a knob interface through which an intent compiler sets the controller's parameters. It also changes some simulator behavior and accounting, which can alter the results of runs that version 1.0 supports, so the ContinuumBench paper's results come from the branch `europar-frame-2026` and the tag `v1.0-europar-frame`.

### Added

- The knob interface `continuum_bench.theta`, with its schema `theta_schema.yaml` shipped in the package: nineteen discrete knobs, their validation, and their projection onto the configuration blocks that the controllers read. A run configuration with a `theta:` block is projected once, when the runner is built. The objective weights are recorded in `score.weights` for an objective computed outside the simulator.
- Per-class sink deadlines (`deadline_by_priority`).
- Tier-priced provisioning cost: `cost.price_per_tier` in the configuration; `cost.worker_seconds_by_tier` and `cost.total_tier_priced_worker_seconds` in the run summary.
- The on-time rate of each task class (`qos.on_time_rate_by_priority`) in the run summary, and each node's tier in the run metadata.
- A configurable MILP placement policy (`placement.milp`): objective weights with a cost term weighted by tier, a residency constraint, an egress budget and a cap on dynamic power.
- The controller `anticipatory`, an autoscaler that pre-warms pools before known windows of load, and the reference controller `constant[:N]`, which holds every pool, or each named pool, at a fixed size.
- An optional second scalable pool in `eo_dual_stream` (`scaling.pools.Fusion`) and optional direct links between nodes of the same tier (`infrastructure.intra_tier`).
- The scenario variants `configs/eo_elastic_bottleneck.yaml`, `configs/eo_placement_stress.yaml` and `configs/eo_two_pool.yaml`.

### Changed

These changes can alter the results of runs that version 1.0 also supports.

- Work that a run destroys (buffer overflow, eviction, retry exhaustion, TTL expiry, or loss in transit to a failed node) now counts as undelivered work in the deadline accounting, so discarding work can no longer improve a controller's on-time rate.
- A message re-sent from a store-and-forward buffer takes a size-aware transfer time, as a fresh send does.
- A message larger than a link's per-epoch budget is admitted alone on an idle link, and a buffer releases such an item at its head; before, it stalled for the rest of the run.
- The MILP planner places unanchored stages on edge and cloud nodes only, wherever it is used. The placement-only MILP controller also falls back to the minimum-latency heuristic when its mapping over-subscribes a node at run time.
- With `placement.replan_on_scale: false`, an autoscaler whose new workers do not fit around the frozen placement re-solves the whole placement once, instead of deferring the scale-up.
- The MILP energy objective counts dynamic power only, since the simulator keeps every node powered whatever the placement.

### Fixed

- The author's name in `LICENSE` and the package metadata: Lanpei Li.

## 1.0.0

The version of the ContinuumBench paper (FRAME 2026 at Euro-Par 2026), tagged `v1.0-europar-frame`, on the branch `europar-frame-2026`.
