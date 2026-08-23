# Continuum Bench

Continuum Bench is a public benchmark package for reproducible placement and autoscaling experiments across cloud-edge continuums with intermittent links.

It packages a focused benchmark core for joint placement, worker-pool scaling, intermittent-link transport, and store-and-forward behavior across cloud, edge, IoT, and space tiers.

ContinuumBench is Eclypse-native by default: application graphs, infrastructure graphs, placement views, structural path lookups, node availability views, route-cost timing, graph update policies, and native placement strategies are taken from Eclypse first. ContinuumBench adds benchmark protocol logic, workload queues, temporary contact reachability, controller policies, reliability and energy metrics, adaptive-controller interfaces, and cross-run analysis on top.

## Install

This package requires Python 3.10+.

It requires `eclypse` (pinned to version 0.8.1, commit `73806b7`) as the substrate for application, infrastructure, placement, routing, and graph update-policy machinery. The package will use the normal environment first, then an `ECLYPSE_HOME` checkout if you set one.

```bash
python3.11 -m venv .venv          # tested with CPython 3.11.8 (requires >=3.10)
source .venv/bin/activate
pip install -e ".[milp,figures,dev]"  # core + scipy/numpy (MILP) + matplotlib (figures) + pytest

# External ECLYPSE substrate, pinned to 0.8.1 (commit 73806b7):
git clone https://github.com/eclypse-org/eclypse && (cd eclypse && git checkout 73806b7 && pip install -e .)
# ...or point to an existing checkout instead of pip-installing it:
export ECLYPSE_HOME=/path/to/eclypse
```

`PyYAML` and `loguru` are installed with this package; the `milp` extra adds `scipy`/`numpy`, the
`figures` extra adds `matplotlib`, and the `dev` extra adds `pytest` for the test suite. `eclypse`
is intentionally treated as an external substrate dependency. For an exact pin of the versions used for the committed results, `pip install -r
requirements-lock.txt`. See **[REPRODUCE.md](REPRODUCE.md)** to regenerate the paper's tables and figures.

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
Runs produced before the current `pending`/`resolved` summary contract and drain-window protocol should be discarded and rerun; the publication analysis path is strict and does not infer those fields from old artifacts.

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

Contact-aware trace-backed configs use packaged contact windows under
`src/continuum_bench/calibration/contact_traces/`. Calibration-only F2 configs
can instead select documented network or service-time profiles without adding
intermittent links. Contact traces are runtime transport overlays: they update
`contact_open`, `contact_bandwidth`, and `contact_latency` on Eclypse edges so
buffering and delivery honor temporary reachability. They do not mutate Eclypse
structural edge availability or model hard link failures.

Analysis uses an `evaluation_regime` axis:
- `F0`: deterministic synthetic baseline.
- `F1`: contact/mobility-aware execution.
- `F2`: calibrated trace-backed execution. F2 does not imply intermittent contact by itself; contact/mobility remains a separate overlay and is enabled only for scenarios whose workflow contains an intermittent boundary.

Energy accounting and node failures are stressor overlays on those regimes, not additional points on the same axis. Existing configs and sweeps may still declare the legacy `realism_tier`; ContinuumBench maps `T0`, `T1`, and `T2` to `F0` with the relevant overlays, `T3` to `F1`, and `T4` to `F2`.

## Scenarios

- `eo_dual_stream`: fuses a space stream and an IoT stream, then routes work through triage into fast and bulk processing paths.
- `driving_v2x`: models a vehicle-to-edge pipeline with perception, tracking, planning, and offload-sensitive execution.
- `split_inference`: models an edge-first inference path with optional cloud refinement.
- `industrial_iot`: models a control-oriented industrial stream with a bulk/archive-style path.

## Controllers

The paper's nine controller variants (CLI `--controller` keys), grouped by family. The paper renames
two of them; the mapping is noted in **bold**.

Placement-only (choose ECLYPSE-audited placements, no scaling):
- `round_robin`: distributes stages cyclically (Eclypse round-robin strategy, extended for benchmark anchor/resource cases).
- `best_fit`: prefers feasible nodes with residual resources (Eclypse best-fit strategy).
- `placement_only:cost_greedy`: greedy over compute and transfer-cost estimates.
- `placement_only:milp`: solver-backed MILP reference over a reduced static snapshot with a linear latency objective (needs the `milp` extra: `pip install -e ".[milp]"`).

Autoscaling (change worker counts, with ECLYPSE-audited placement replanning):
- `hpa`: HPA-style queue-utilization autoscaler.
- `keda`: KEDA-style queue-triggered autoscaler.

Joint:
- `separated_loops`: the paper's **joint scale-and-place heuristic** (threshold scaling + periodic replanning in one step).

Rule-based baselines (deterministic, no learning):
- `learned_stub`: the paper's **Feature-rule** controller (a fixed policy over normalized benchmark features).
- `rl_meta`: the paper's **Meta-rule** controller (selects scale direction, replanning, and a placement strategy from a discrete action space).

The registry also accepts extreme/extra placement baselines (`static`, `no_scaling`, `all_local`,
`all_edge`, `all_cloud`) used in supporting sweeps. To add your own controller, see
[`src/continuum_bench/controllers/README.md`](src/continuum_bench/controllers/README.md).

## Eclypse Boundary

Eclypse is authoritative for substrate behavior: application and infrastructure assets, symbolic placement layers, residual placement views, resource feasibility, structural path lookup, path resource aggregation, route-cost transport delay, and update-policy graph mutation. ContinuumBench keeps only runtime behavior that Eclypse does not define as a substrate primitive: queue and buffer dynamics, temporary contact reachability, epoch-level link budget accounting, stochastic node-failure scenario generation, energy integration over time from Eclypse-declared power assets, reward logging, and offline analysis.

Epoch-level resource records expose both views:
- `resources.reserved`: Eclypse placement-view reservations for active services.
- `resources.active_runtime`: queue-weighted active runtime demand used by the benchmark loop.
- the legacy top-level `resources.cpu`, `resources.ram`, `resources.storage`, and `resources.nodes` fields mirror `active_runtime` for backward-compatible analysis scripts.

## Reproduce The Paper

```bash
make smoke       # fast end-to-end check (sota_pilot, 3 seeds; minutes)
make reproduce   # full reproduction: all sweeps + analysis + figures (~1 CPU-hour)
make test        # regression suite, including the pinned headline numbers
```

`make reproduce` writes the main comparison to `runs/main/analysis/grouped_strict.csv` and the
figures to `figs/`. Override the interpreter, output root, or figure directory with
`PY=`, `RUNS=`, `FIGS=`. **[REPRODUCE.md](REPRODUCE.md)** maps each paper artifact to the sweep
manifest and command that produce it.

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
collection errors. `tests/test_golden_headline.py` pins the paper's headline numbers against
`tests/golden/` when local run artifacts are present.

## Output Files

Each run directory includes:

- `metadata.json`: run metadata such as scenario, controller, and seed.
- `epochs.jsonl`: detailed epoch-level records.
- `decision_trajectory.jsonl`: epoch-level state, action, outcome, and reward records.
- `epochs.csv`: compact epoch-level metrics.
- `summary.json`: aggregate metrics for latency, throughput, SLOs, resource utilization, energy, reliability, freshness, buffering, churn, migrations, and controller overhead. Reliability reports completed, failed, and pending work together with completed-or-failed accounting so low completion cannot masquerade as a good latency result.

## Public Scope

This public benchmark repo contains the code, configurations, and analysis utilities required to run the benchmark scenarios, protocols, and baseline families.
