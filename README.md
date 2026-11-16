<h1 align="center">ContinuumBench</h1>

<p align="center">
  <a href="https://arxiv.org/abs/2609.08946"><img src="https://img.shields.io/badge/arXiv-2609.08946-b31b1b.svg" alt="arXiv 2609.08946"></a>
  <img src="https://img.shields.io/badge/FRAME%202026-Euro--Par%202026-4b44ce.svg" alt="FRAME 2026 at Euro-Par 2026">
</p>

<p align="center">
  <a href="https://arxiv.org/abs/2611.10292"><img src="https://img.shields.io/badge/arXiv-2611.10292-b31b1b.svg" alt="arXiv 2611.10292"></a>
  <img src="https://img.shields.io/badge/RISE--DL%202026-IoT%202026-4b44ce.svg" alt="RISE-DL 2026 at IoT 2026">
</p>

<p align="center">
  <a href="https://scholar.google.it/citations?user=PnkoFQYAAAAJ&hl=en">Lanpei Li</a><sup>1,2</sup>,
  <a href="https://scholar.google.it/citations?user=L-L69K8AAAAJ&hl=en">Antonino Vaccarella</a><sup>1,2</sup>,
  <a href="https://scholar.google.it/citations?user=rQLINtQAAAAJ&hl=en">Vincenzo Lomonaco</a><sup>3</sup>,
  <a href="https://orcid.org/0000-0002-7937-4157">Massimo Coppola</a><sup>1</sup>
</p>

<p align="center">
  <sup>1</sup>ISTI, National Research Council of Italy (CNR) &nbsp;&middot;&nbsp;
  <sup>2</sup>University of Pisa &nbsp;&middot;&nbsp;
  <sup>3</sup>LUISS University
</p>

ContinuumBench is a public benchmark package for reproducible placement and autoscaling experiments across cloud-edge continuums with intermittent links.

It packages a focused benchmark core for joint placement, worker-pool scaling, intermittent-link transport, and store-and-forward behavior across cloud, edge, IoT, and space tiers.

ContinuumBench is ECLYPSE-native by default: application graphs, infrastructure graphs, placement views, structural path lookups, node availability views, route-cost timing, graph update policies, and native placement strategies are taken from ECLYPSE first. ContinuumBench adds benchmark protocol logic, workload queues, temporary contact reachability, controller policies, reliability and energy metrics, adaptive-controller interfaces, and cross-run analysis on top.

## Versions

| Version | Where | Paper |
|---|---|---|
| 1.0 | branch `europar-frame-2026`, tag `v1.0.1` | [ContinuumBench: Benchmarking Joint Autoscaling and Placement Across Evaluation Regimes in the Cloud-Edge Continuum](https://arxiv.org/abs/2609.08946) (FRAME 2026 at Euro-Par 2026) |
| 1.1 | branch `main`, tags `v1.1.0` and `v1.1.1` | [Continual Instruction Tuning for LLM-based Management of Continuum Resources](https://arxiv.org/abs/2611.10292) (RISE-DL 2026 at IoT 2026) |

This README calls them the ContinuumBench paper and the intent-compiler paper. The second trains an intent compiler, a language model that translates a management intent in natural language into the parameters of a controller. Version 1.1 adds three scenario variants, the controllers they use, and a knob interface through which an intent compiler sets the controller's parameters. It also changes some simulator behavior (see [CHANGELOG.md](CHANGELOG.md)), so the ContinuumBench paper's results come from version 1.0.1, on the branch `europar-frame-2026`. Version 1.1.1 brings the ContinuumBench paper's scenario configs, sweeps and analysis in line with version 1.0.1. The intent compiler itself, its data and its trained adapters are in the companion repository [continuum-intent-compiler](https://github.com/lilanpei/continuum-intent-compiler).

## Install

This package requires Python 3.11 to 3.13, the versions that ECLYPSE 0.8.1 supports.

It requires `eclypse` (pinned to version 0.8.1, commit `73806b7`) as the substrate for application, infrastructure, placement, routing, and graph update-policy machinery. The package will use the normal environment first, then an `ECLYPSE_HOME` checkout if you set one.

```bash
python3.11 -m venv .venv          # tested with CPython 3.11.8 (3.11 to 3.13)
source .venv/bin/activate
pip install -e ".[milp,figures,dev]"  # core + scipy/numpy (MILP) + matplotlib (figures) + pytest

# External ECLYPSE substrate, pinned to 0.8.1 (commit 73806b7):
git clone https://github.com/eclypse-org/eclypse && (cd eclypse && git checkout 73806b7 && pip install -e .)
# ...or point to an existing checkout whose dependencies are installed:
export ECLYPSE_HOME=/path/to/eclypse
```

`PyYAML` and `loguru` are installed with this package; the `milp` extra adds `scipy`/`numpy`, the
`figures` extra adds `matplotlib`, and the `dev` extra adds `pytest` for the test suite. `eclypse`
is intentionally treated as an external substrate dependency. To pin the exact versions of
`requirements-lock.txt`, run `pip install -r requirements-lock.txt` before the commands above. See **[REPRODUCE.md](REPRODUCE.md)** to regenerate the ContinuumBench paper's tables and findings.

## What This Repo Contains

- a standalone `continuum_bench` Python package under `src/`
- sample benchmark configs and trace-backed variants under `configs/`
- a focused regression test suite under `tests/`
- baseline controllers spanning trivial, heuristic, and oracle-style references
- publication sweep and analysis pipelines under `sweeps/` and `analysis/`

## Readability Map

- `src/README.md`: explains the `src/` layout and points to the installable package.
- `src/continuum_bench/README.md`: maps the package entry points and the main implementation directories.
- `configs/README.md`: indexes the runnable benchmark configs and trace-backed variants.
- `sweeps/README.md`: explains the publication sweep manifests and batch experiment entry point.
- `calibration/README.md`: documents the public calibration assets and conversion workflow.
- `splits/README.md`: explains the benchmark train/validation/test split files for controller-training studies.
- `analysis/README.md`: explains the offline aggregation and plotting path for publication sweeps.
- `docs/README.md`: reserves space for longer-form project documentation beyond the targeted READMEs.
- `tests/README.md`: summarizes the regression suite and where new checks belong.

## Run Benchmarks

```bash
python -m continuum_bench.cli run --config configs/eo_small.yaml --controller separated_loops --seed 0 --out runs/
python -m continuum_bench.cli run --config configs/driving_small.yaml --controller best_fit --seed 0 --out runs/
python -m continuum_bench.cli run --config configs/eo_small.yaml --controller round_robin --seed 0 --out runs/
```

`epochs` is the workload-arrival window. Optional `drain_epochs` extends the run with no new source arrivals so queued, in-flight, or buffered tasks can complete before summary metrics are computed. Publication sweep manifests use this split explicitly.

## Summarize Results

```bash
python -m continuum_bench.cli summarize --run_dir runs/<run_id>
```

This rewrites `summary.json` for an existing run from the epoch-level log output.
The analysis is strict: it does not infer the `pending` and `resolved` fields or the drain window for a run whose summary lacks them, so such a run must be rerun.

## Sample Configs

- `configs/eo_small.yaml`: EO dual-stream benchmark with intermittent contact windows and a scalable bulk path.
- `configs/driving_small.yaml`: driving/V2X benchmark with scalable offload workers.
- `configs/split_inference_small.yaml`: split-inference benchmark with an edge-complete fast path and cloud refinement.
- `configs/industrial_iot_small.yaml`: industrial IoT benchmark with a control path and a bulk/archive-style path.

Trace-backed variants are also provided:
- `configs/eo_trace_small.yaml`
- `configs/driving_trace_small.yaml`
- `configs/split_inference_trace_small.yaml`
- `configs/industrial_iot_trace_small.yaml`

Scenario variants of the intent-compiler paper (version 1.1), each built on `eo_dual_stream`:
- `configs/eo_elastic_bottleneck.yaml` (the paper's elastic variant): deciding how many workers to run over time; one elastic pool is the only bottleneck under a peaky load.
- `configs/eo_placement_stress.yaml` (the placement variant): deciding where stages run; tight CPU and a thin edge-cloud link make placement the limiting constraint.
- `configs/eo_two_pool.yaml` (the two-pool variant): deciding how a shared core budget is split between two worker pools.

`configs/eo_placement_stress.yaml` is also the placement-stress variant of the ContinuumBench paper's Finding 3, which compares it with its control, `configs/eo_placement_control.yaml` (the CPU, the edge-cloud link and the frame sizes of `eo_small.yaml`).

In the eight scenario configs, the placement-stress variant, its control and the two-pool variant, the edge nodes are linked directly over a LAN (1 ms, 10 Gbps; `infrastructure.intra_tier`). The elastic-bottleneck variant keeps the tiered graph alone, in which two edge nodes reach each other only through the cloud.

Contact-aware trace-backed configs use packaged contact windows under
`src/continuum_bench/calibration/contact_traces/`. Calibration-only configs of the trace-backed
regime (F2, below) can instead select documented network or service-time profiles without adding
intermittent links. Contact traces are runtime transport overlays: they update
`contact_open`, `contact_bandwidth`, and `contact_latency` on ECLYPSE edges so
buffering and delivery honor temporary reachability. They do not mutate ECLYPSE
structural edge availability or model hard link failures.

Analysis uses an `evaluation_regime` axis:
- `F0`: deterministic synthetic baseline.
- `F1`: contact/mobility-aware execution.
- `F2`: calibrated trace-backed execution. F2 does not imply intermittent contact by itself; contact/mobility remains a separate overlay and is enabled only for scenarios whose workflow contains an intermittent boundary.

Energy accounting and node failures are stressor overlays on those regimes, not additional points on the same axis. The configs and sweeps declare a `realism_tier`, which ContinuumBench maps onto this axis: `T0`, `T1`, and `T2` to `F0` with the relevant overlays, `T3` to `F1`, and `T4` to `F2`. The ContinuumBench paper's regimes R1 and R2 are `T0` and `T4`.

## Scenarios

- `eo_dual_stream`: fuses a space stream and an IoT stream, then routes work through triage into fast and bulk processing paths.
- `driving_v2x`: models a vehicle-to-edge pipeline with perception, tracking, planning, and offload-sensitive execution.
- `split_inference`: models an edge-first inference path with optional cloud refinement.
- `industrial_iot`: models a control-oriented industrial stream with a bulk/archive-style path.

## Controllers

The ContinuumBench paper's nine controller variants (CLI `--controller` keys), grouped by family. The
paper names three of them differently; its names are in **bold**.

Placement-only (choose ECLYPSE-audited placements, no scaling):
- `round_robin`: distributes stages cyclically (ECLYPSE round-robin strategy, extended for benchmark anchor/resource cases).
- `best_fit`: prefers feasible nodes with residual resources (ECLYPSE best-fit strategy).
- `placement_only:cost_greedy`: greedy over compute and transfer-cost estimates.
- `placement_only:milp`: solver-backed MILP reference over a reduced static snapshot with a linear latency objective (needs the `milp` extra: `pip install -e ".[milp]"`). In version 1.1 it can instead take a weighted objective with tier price weights and hard constraints, set through the knob interface.

Autoscaling (change worker counts, with ECLYPSE-audited placement replanning):
- `hpa`: HPA-style queue-utilization autoscaler.
- `keda`: KEDA-style queue-triggered autoscaler.

Joint:
- `separated_loops`: the paper's **Joint heuristic** (threshold scaling + periodic replanning in one step).

Rule-based baselines (deterministic, no learning):
- `learned_stub`: the paper's **Feature-rule** controller (a fixed policy over normalized benchmark features).
- `rl_meta`: the paper's **Meta-rule** controller (selects scale direction, replanning, and a placement strategy from a discrete action space).

Added in version 1.1:
- `anticipatory`: an autoscaler that pre-warms worker pools before known windows of load (contact windows or scheduled bursts) and scales them back between windows; the intent-compiler paper uses it on its elastic and two-pool variants.
- `constant`, `constant:N` or `constant:Fusion=2,BulkPath=6`: a reference controller that holds every pool, or each named pool, at a fixed size (a bare `constant` holds the pool maximum).

The registry also accepts extreme/extra placement baselines (`static`, `no_scaling`, `all_local`,
`all_edge`, `all_cloud`) used in supporting sweeps; `no_scaling` with every pool at its maximum is
the over-provisioned reference of the load sweep. Any placement strategy can run as a
placement-only controller, `placement_only:<strategy>`, as in the placement sweep. To add your own
controller, see [`src/continuum_bench/controllers/README.md`](src/continuum_bench/controllers/README.md).

## Knob Interface

Version 1.1 lets an intent compiler set the controller's parameters once, as a vector `theta` of nineteen discrete knobs, instead of acting at every step. The schema [`src/continuum_bench/theta_schema.yaml`](src/continuum_bench/theta_schema.yaml) declares each knob's levels, its default and its consumers: the per-class deadlines of the runner; the scaling policy of the anticipatory autoscaler and the pool limits; and the placement policy, tier price weights and hard constraints of the MILP (`placement_only:milp`). The objective weights are recorded in `score.weights` for the intent's declared objective, which is computed outside the simulator from the run summary; the run's tier-priced cost uses the deployment's own `cost.price_per_tier`, not the price weights.

`continuum_bench.theta.validate_theta` rejects a value outside its declared levels as invalid, and `apply_theta` projects a valid vector onto the configuration. A configuration that carries a `theta:` block is projected when the runner is built; every knob left out takes its default, and the projection overwrites the configuration's own values in the blocks it sets (per-class deadlines, anticipatory settings, pool caps and floors, MILP policy).

```python
from continuum_bench.runner import ContinuumBenchRunner, load_config
from continuum_bench.theta import default_theta

config = load_config("configs/eo_placement_stress.yaml")
config["theta"] = {**default_theta(), "w_cost": 1.0, "tier_price_cloud": 4.0}
runner = ContinuumBenchRunner(config=config, controller_name="placement_only:milp", seed=0, out_root="runs")
summary = runner.run()["summary"]
print(summary["qos"]["on_time_rate_by_priority"], summary["cost"]["worker_seconds_by_tier"])
```

A knob vector can be valid but infeasible for a deployment: when the MILP's hard constraints cannot be met, `placement_only:milp` raises a `ValueError` ("could not place active services").

## ECLYPSE Boundary

ECLYPSE is authoritative for substrate behavior: application and infrastructure assets, symbolic placement layers, residual placement views, resource feasibility, structural path lookup, path resource aggregation, route-cost transport delay, and update-policy graph mutation. ContinuumBench keeps only runtime behavior that ECLYPSE does not define as a substrate primitive: queue and buffer dynamics, temporary contact reachability, epoch-level link budget accounting, stochastic node-failure scenario generation, energy integration over time from ECLYPSE-declared power assets, reward logging, and offline analysis.

Epoch-level resource records expose both views:
- `resources.reserved`: ECLYPSE placement-view reservations for active services.
- `resources.active_runtime`: queue-weighted active runtime demand used by the benchmark loop.
- the legacy top-level `resources.cpu`, `resources.ram`, `resources.storage`, and `resources.nodes` fields mirror `active_runtime` for backward-compatible analysis scripts.

## Reproduce The Papers

Reproduce the ContinuumBench paper with version 1.0.1, the tag `v1.0.1` on the branch
`europar-frame-2026`, whose **REPRODUCE.md** maps every table and finding to its sweep and gives the
values to expect:

```bash
git checkout v1.0.1
make reproduce   # the paper's sweeps, their aggregation, and every number
make test        # regression suite, including the pinned headline numbers
```

`main` runs the same sweeps; **[REPRODUCE.md](REPRODUCE.md)** lists the two findings that differ in
version 1.1. The intent-compiler paper is reproduced from the companion repository
[continuum-intent-compiler](https://github.com/lilanpei/continuum-intent-compiler).

## Extend The Benchmark

Adding a controller or a scenario does not require editing the runner: drop in a
`controllers/extensions_local.py` or `scenarios/extensions_local.py` module and the corresponding
`extensions.py` hook loads it on demand. See
[`src/continuum_bench/controllers/README.md`](src/continuum_bench/controllers/README.md) and
[`src/continuum_bench/scenarios/README.md`](src/continuum_bench/scenarios/README.md).

## Verification

```bash
pytest tests -q
```

The suite is deterministic and fast. ECLYPSE-only tests are skipped automatically when `eclypse` is
not importable, so a clean checkout without the substrate reports `passed`/`skipped` rather than
collection errors. `tests/test_golden_headline.py` pins the ContinuumBench paper's headline numbers
against `tests/golden/` once `make reproduce` has produced `runs/main/analysis`.

## Output Files

Each run directory includes:

- `metadata.json`: run metadata such as scenario, controller, and seed.
- `epochs.jsonl`: detailed epoch-level records.
- `decision_trajectory.jsonl`: epoch-level state, action, outcome, and reward records.
- `epochs.csv`: compact epoch-level metrics.
- `summary.json`: aggregate metrics for latency, throughput, SLOs, resource utilization, energy, reliability, freshness, buffering, churn, migrations, and controller overhead. Reliability reports completed, failed, and pending work together with completed-or-failed accounting so low completion cannot masquerade as a good latency result. With version 1.1, the summary also reports worker-seconds by tier and their tier-priced total, and the on-time rate of each task class.
- `provenance/`: for a run of a sweep, the rendered configuration (`config.yaml`) and the manifest run and seed that produced it (`sweep.json`).

## Public Scope

This public benchmark repo contains the code, configurations, and analysis utilities required to run the benchmark scenarios, protocols, and baseline families.

## Citation

If you use ContinuumBench, please cite the ContinuumBench paper. If you use the knob interface or the scenario variants of version 1.1, please also cite the intent-compiler paper.

```bibtex
@inproceedings{li2026continuumbench,
  title         = {{ContinuumBench}: Benchmarking Joint Autoscaling and Placement Across Evaluation Regimes in the Cloud-Edge Continuum},
  author        = {Li, Lanpei and Vaccarella, Antonino and Lomonaco, Vincenzo and Coppola, Massimo},
  booktitle     = {Euro-Par 2026: Parallel Processing Workshops (6th Workshop on Flexible Resource and Application Management on the Edge, FRAME 2026)},
  series        = {Lecture Notes in Computer Science},
  publisher     = {Springer},
  year          = {2026},
  eprint        = {2609.08946},
  archivePrefix = {arXiv},
  primaryClass  = {cs.DC}
}

@inproceedings{li2026intentcompiler,
  title         = {Continual Instruction Tuning for {LLM}-based Management of Continuum Resources},
  author        = {Li, Lanpei and Vaccarella, Antonino and Lomonaco, Vincenzo and Coppola, Massimo},
  booktitle     = {The 16th International Conference on the Internet of Things (IOT 2026)},
  publisher     = {ACM},
  year          = {2026},
  doi           = {10.1145/3860552.3860597},
  eprint        = {2611.10292},
  archivePrefix = {arXiv},
  primaryClass  = {cs.DC}
}
```

## License

The code is released under the MIT License (see [LICENSE](LICENSE)).
