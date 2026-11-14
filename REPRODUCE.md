# Reproducing the ContinuumBench paper

This is the authoritative guide for regenerating every table and figure in the paper. The pipeline
is deterministic and file-based: **sweep manifests → run directories → aggregated CSV/JSON →
tables/figures**.

Run this guide on the branch `europar-frame-2026` (the tag `v1.0-europar-frame` holds the same
code, without the branch's test dependency `anyio`). Version 1.1,
on `main`, changes some simulator behavior and accounting (see [CHANGELOG.md](CHANGELOG.md)), so it
does not reproduce the paper's numbers.

## 1. Environment

Tested with **CPython 3.11.8**. Use an explicit interpreter within the range that ECLYPSE 0.8.1 supports, 3.11 to 3.13:

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

Determinism: every run uses fixed integer seeds (main comparison: 0–9; sensitivity sweeps: 0–2).
Optionally `export PYTHONHASHSEED=0` as defensive hygiene.

## 2. One-command paths

```bash
make smoke       # fast end-to-end check (sota_pilot, 3 seeds, 80+40 epochs; a few minutes)
make reproduce   # full reproduction: all sweeps + analysis + figures (long; ~1800 simulations)
make test        # regression suite (pins the headline numbers; see tests/test_golden_headline.py)
```

`make reproduce` writes Table 1 data to `runs/main/analysis/grouped_strict.csv` and all
figures to `figs/`. Expect the full matrix to take roughly one CPU-hour on a recent laptop
(720 main runs + sensitivity/ladder sweeps, each 120+60 or 80+40 epochs).

## 3. Artifact → command map

Each artifact derives from one sweep manifest, aggregated with `analysis/aggregate_runs.py` and
`analysis/tables.py`, then rendered by `analysis/figures.py`:

| Paper artifact | Sweep manifest |
| --- | --- |
| Table 1 (main comparison); Fig. main F2 tradeoff; control-dimension ablation; controller overhead; EO trajectory | `sweeps/main.yaml` |
| Rank-stability figure | `sweeps/realism_ladder.yaml` |
| Load-sensitivity figure + sensitivity-table load columns | `sweeps/sensitivity_load.yaml` |
| Contact-sensitivity figure | `sweeps/sensitivity_contact.yaml` |
| Sensitivity-table failure columns | `sweeps/sensitivity_failure.yaml` |
| Delay-breakdown figure | `sweeps/delay_breakdown.yaml` |
| Solver-backed MILP reference | `sweeps/milp_reference.yaml` |

The score-robustness claims ("two of eight contexts change selection", 81-point grid, ≥0.14 SLO
reduction) come from `analysis/score_sensitivity.py` over `main`. The paired Wilcoxon
significance result (SLO reduction significant in 8/8 cells, completion in 7/8) is reproduced by
`analysis/significance.py --aggregate-csv runs/main/analysis/aggregate.csv`.

## 4. Verifying a reproduction

`tests/test_golden_headline.py` pins the headline Table 1 cells and the score-robustness invariants
against `tests/golden/main_headline.json`. After `make reproduce`, run `make test`: the golden
test recomputes the numbers from your fresh `runs/main/analysis` and asserts they match.

The `runs/` tree is git-ignored, so a fresh clone reproduces from scratch. On a clone without it
that one test reports as skipped rather than failing, which is the expected result before you have
run any sweeps. If a code change legitimately moves the numbers, regenerate the snapshot from
`runs/main/analysis`.
