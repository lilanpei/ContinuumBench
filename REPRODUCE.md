# Reproducing the ContinuumBench paper

This guide maps every table and finding of the ContinuumBench paper (FRAME 2026 at Euro-Par 2026)
to the sweep that produces it, the command that prints its numbers, and the values to expect. The
pipeline is deterministic and file-based:
**sweep manifests → run directories → printed values**.

Use version 1.0.1: this branch, `europar-frame-2026`, or its tag `v1.0.1-europar-frame`. The first
release, tag `v1.0-europar-frame`, lacks the simulator fixes and the topology with which the
paper's results were produced (see [CHANGELOG.md](CHANGELOG.md)), and version 1.1, on `main`,
changes further simulator behavior.

## 1. Environment

Use an interpreter within the range that ECLYPSE 0.8.1 supports, 3.11 to 3.13 (tested with
CPython 3.11.8):

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-lock.txt  # optional: the exact versions of the paper's runs
pip install -e ".[milp,figures,dev]"  # core + scipy/numpy (MILP) + matplotlib (figures) + pytest
```

The **ECLYPSE** substrate is an external dependency, pinned to **eclypse 0.8.1 (commit `73806b7`)**.
Install it with pip (or set `ECLYPSE_HOME` to a checkout whose dependencies are installed):

```bash
git clone https://github.com/eclypse-org/eclypse && (cd eclypse && git checkout 73806b7 && pip install -e .)
```

Every run uses fixed integer seeds, listed in each manifest, so a rerun gives the same results.

## 2. Run everything

```bash
make reproduce   # the paper's eight sweeps (about 2,200 simulations), their aggregation, and every number
make test        # the regression suite; the golden test then checks the main comparison
```

`make reproduce` runs the sweeps one after another, one to two CPU-hours in total; the longest are
the re-planning ablation and the main comparison. The sweeps are independent, so they can also run
in parallel, one process each, followed by the analysis:

```bash
python -m continuum_bench.cli sweep --manifest sweeps/<sweep>.yaml --out runs/<sweep>
make analysis findings
```

A sweep adds its runs to its output directory, so delete `runs/<sweep>` before running a sweep
again; `analysis/findings.py` stops with a message when a sweep has a missing or repeated run.

## 3. The paper's tables and findings

Each command below prints the numbers of one result from the run directories of its sweep; `make
findings` runs them all. A sweep runs `n` simulations: its runs times its seeds.

| Paper | Sweep (simulations) | Command |
| --- | --- | --- |
| Table 1, scenario scale | the configs of `configs/` | – |
| Table 2, controllers | the `--controller` keys of README.md, "Controllers" | – |
| Table 3 and the text around it | `main` (720) | `python analysis/findings.py main --sweep-dir runs/main` |
| Score robustness, Section 5 | `main` | `make analysis` (`analysis/score_sensitivity.py`) |
| Finding 1 | `replan_ablation` (360) | `python analysis/findings.py replan --sweep-dir runs/replan_ablation` |
| Finding 1, the MILP re-planner | `replan_milp` (30) | `python analysis/findings.py replan-milp --sweep-dir runs/replan_milp` |
| Finding 2 | `load_sweep` (480) | `python analysis/findings.py load --sweep-dir runs/load_sweep` |
| Finding 3 | `placement_sweep` (90) | `python analysis/findings.py placement --sweep-dir runs/placement_sweep` |
| Finding 4 | `sensitivity_failure` (216) and `main` | `python analysis/findings.py failure --sweep-dir runs/sensitivity_failure` |
| Stressor rank changes | `sensitivity_contact` (108), `sensitivity_load` (216) | `python analysis/findings.py stressors --contact-dir runs/sensitivity_contact --load-dir runs/sensitivity_load` |

### Values to expect

**Table 3.** All sixteen cells, completion / SLO-violation rate / p95 latency (s), as the paper
prints them. Where placement-only controllers score exactly the same, the script names the tied
ones beside the representative.

**Around Table 3.** HPA ranks first among the scale-capable controllers in 3 of the 8
scenario-regime pairs and is within 0.01 of the first elsewhere. Seed by seed, it has the lower
SLO-violation rate in 80 of 80 comparisons and equal or higher completion in 79 of 80 (the
exception is one seed of Industrial IoT R1, 0.995 against 1.000). The largest completion gain is
in EO R2, from 0.22 to 0.53. The largest standard deviations over seeds are 0.062 for completion
and 0.214 for the SLO-violation rate. In Industrial IoT under R2, HPA and KEDA both complete 1.00
of the tasks, with SLO-violation rates of 0.01 and 0.57.

**Score robustness.** Over the 81-point weight grid, the selected representative changes in one of
the eight scenario-regime contexts (Driving V2X R1, between HPA and the joint heuristic), and the
scale-capable representative keeps the lower SLO-violation rate in all of them. The check chooses
the scale-capable representative among HPA, KEDA and the joint heuristic.

**Decision times.** The paper's values are a p95 decision time per epoch below 3 ms (under 0.3% of
the 1 s control period) except for the MILP, 8 to 25 ms, and about 181 ms for HPA on the 48-node
infrastructures of the re-planning ablation. They depend on the machine: one cluster core gave
7.7 ms, 28 to 74 ms and 339 ms.

**Finding 1.** Across the 24 configurations, the three schedules give the same completion
(largest difference 0), no schedule relocates a placed stage, and the synthesized infrastructures
of 22 to 48 nodes give the same completion as each scenario's default infrastructure of 4 to 6
nodes. The MILP re-planner, on EO with twice the load and 8-core edge nodes, relocates about 55
workers per run when it re-plans after every scale event and periodically, and so raises completion
by 0.045, from 0.211 when it places only newly activated workers to 0.255.

**Finding 2.** As a share of the over-provisioned reference's completion: HPA 99 to 100%, the
joint heuristic 97 to 100%, KEDA 89 to 100%, and placement-only control (min-latency and the MILP)
44 to 100%, which falls further behind at 2.0x load in every scenario except EO. In 14 of the 16
scenario-load combinations placement-only control misses some task; in all 14, every
scale-capable controller completes more than placement-only control in every seed. At fixed
capacity the MILP and the min-latency heuristic give the same completion, and the same mapping in
99.7% of the epochs, up to a relabeling of identical nodes.

**Finding 3.** On-time delivery spreads the six strategies by 0.42 on the placement-stress
variant, by 0.12 on its control and by 0.01 on unmodified EO. On the variant, cost-greedy completes
102% as many tasks as the three strategies that tie for the best on-time delivery (min-latency,
nearest-feasible-edge and dependency-aware), but delivers 6% of its tasks on time against their 47%.

**Finding 4.** Scored over completed tasks only, the violation rate is lower with node failures
(p = 0.08) than without them in 21 of 24 configurations; counting the unfinished tasks, in one
(Best-fit in Industrial IoT). The HPA and KEDA values of Industrial IoT under R2 are those of
Table 3 above.

**Stressor rank changes.** From the mildest to the most severe level, contact stress changes 3 of
6 ranks in EO and 4 of 6 in Driving V2X; load stress changes 3 of 6 in Industrial IoT and none in
Split inference.

### Where a printed value differs from the paper

- Table 3, Driving V2X R1: the four placement-only controllers score exactly the same, so the
  selection rule's alphabetical tie-break names Best-fit where the paper prints Cost-greedy. The
  cell's values are the paper's.
- Finding 3, unmodified EO: the paper gives an on-time spread of 0.00; the sweep gives 0.01 (from
  0.154 for best-fit to 0.165, at EO's 100 s deadline). The finding stands: placement changes
  on-time delivery only when it is the bottleneck.
- Finding 1: the paper gives the node counts as 6, 24 and 48, the counts of EO and Driving V2X.
  Split inference and Industrial IoT run on 4 and 5 nodes, and their synthesized infrastructures
  on 22 and 44.
- Finding 2: the paper says that the MILP returns the same mapping as the min-latency heuristic. The
  two give the same completion in every run, and the same mapping in 14,360 of the 14,400 epochs
  (99.7%), up to a relabeling of identical nodes.
- Decision times depend on the machine (above).

## 4. Definitions that the analysis uses

- **On-time delivery** is A/G: the tasks completed by their deadline over the generated tasks,
  with each run's own sink deadline (12 s on the placement-stress variant and its control).
- **Load factor:** a run's mean arrival rate over that of the scenario's R1 config.
- **Over-provisioned reference:** `no_scaling` with every pool's minimum, initial and maximum worker
  counts at the pool maximum, so all workers run from the start.
- **Non-saturated combination:** placement-only control misses at least one task in some seed.
- **Failure inversion:** the rate at p = 0.08 is below the rate without failures, as a mean over
  seeds. "Over completed tasks" is the share of completed tasks that finished late; the
  completion-aware rate also counts the unfinished tasks U.
- **Stressor rank change:** a controller's rank by the completion-aware score at the mildest level
  against the most severe one (contact: the mild and the stress trace).
- **Finding 1's MILP re-planner:** the fixed schedule places only newly activated workers; the
  coupled one re-plans after every scale event and periodically.

## 5. Verifying a reproduction

`tests/test_golden_headline.py` pins the cells behind Table 3 and the score-robustness values
against `tests/golden/main_headline.json`. After `make reproduce`, run `make test`: the golden test
recomputes them from `runs/main/analysis` and asserts that they match; it reads that directory
only, so with another `RUNS=` it reports as skipped. The `runs/` tree is git-ignored, so on a fresh
clone that test also reports as skipped until you have run the sweeps.

## 6. Further sweeps

`make extra-sweeps` runs three sweeps that the paper does not report: the evaluation regimes and
overlays step by step (`realism_ladder`), the per-cause delay of completed tasks
(`delay_breakdown`) and a compact MILP reference (`milp_reference`). `make figures` then plots the
main, stressor and further sweeps, and `analysis/significance.py` runs paired Wilcoxon tests of the
main comparison.
