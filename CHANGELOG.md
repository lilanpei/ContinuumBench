# Changelog

## 1.0.1 (2026-11-15)

Version 1.0.1 is on the branch `europar-frame-2026` and the tag `v1.0.1-europar-frame`. It gives the ContinuumBench paper's results, and it adds the sweeps and the analysis of the paper's findings. [REPRODUCE.md](REPRODUCE.md) maps every table and finding to its sweep, gives the values to expect and lists the few printed values that differ.

### Changed

These changes alter the results of some runs of version 1.0.0.

- In every scenario config the edge nodes are linked directly over a LAN (1 ms, 10 Gbps), as the paper describes, through the new option `infrastructure.intra_tier`. Before, two edge nodes reached each other only through the cloud.
- The Driving V2X configs have no space node, so the scenario runs on six nodes (0/3/2/1), as the paper's Table 1 gives. Before, they declared one.
- Work that a run destroys (buffer overflow, eviction, retry exhaustion, TTL expiry, or loss in transit to a failed node) counts as undelivered work in the deadline accounting: a discarded task now counts as a violation instead of dropping out of the SLO-violation rate.
- A message re-sent from a store-and-forward buffer takes a size-aware transfer time, as a fresh send does.
- A message larger than a link's per-epoch budget is admitted alone on an idle link, and a buffer releases such an item at its head. Before, it stalled for the rest of the run.

These change the tools, not the results:

- `make reproduce` runs the paper's sweeps, aggregates the main and stressor sweeps and prints every number with `analysis/findings.py`. The figures, which the paper does not include, come from `make extra-sweeps` and `make figures`; `make analysis` no longer runs `analysis/significance.py`.
- `analysis/score_sensitivity.py` writes its summary beside `--grouped-csv` by default and prints its result; `analysis/tables.py` warns about cells that complete no work.
- An unknown tier name under `infrastructure.intra_tier` raises an error.

### Added

- The sweeps of Findings 1 to 3: `sweeps/replan_ablation.yaml`, `sweeps/replan_milp.yaml`, `sweeps/load_sweep.yaml` and `sweeps/placement_sweep.yaml`, with the placement-stress variant `configs/eo_placement_stress.yaml` and its control `configs/eo_placement_control.yaml`.
- `analysis/findings.py`, which prints the numbers of each finding and of the stressor sweeps from their run directories, and stops when a sweep has a missing or repeated run.
- `provenance/sweep.json` in each run directory of a sweep: the manifest run and the seed that produced it.

### Fixed

- A task sent on a hop that has no store-and-forward buffer counts as lost, with the node-failure losses, and as an SLO violation, which can change the results of runs where this happens. Before, it disappeared from every count.
- The supported Python versions: 3.11 to 3.13, as ECLYPSE 0.8.1 requires.
- The test dependency `anyio` in the `dev` extra.
- The author's name in `LICENSE` and the package metadata: Lanpei Li.

## 1.0.0

The first release of the ContinuumBench paper's code (FRAME 2026 at Euro-Par 2026), tagged `v1.0-europar-frame`.
