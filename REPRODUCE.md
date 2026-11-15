# Reproducing the papers

## The ContinuumBench paper

Reproduce the ContinuumBench paper (FRAME 2026 at Euro-Par 2026) with version 1.0.1:

```bash
git checkout v1.0.1-europar-frame
```

Its `REPRODUCE.md` maps every table and finding of the paper to the sweep that produces it, the
command that prints its numbers, and the values to expect.

`main` carries the same scenario configs, sweeps and analysis, so `make reproduce` also runs here.
Version 1.1 changes some simulator behavior for the intent-compiler paper (see
[CHANGELOG.md](CHANGELOG.md), 1.1.0). On `main`, every table and finding gives the values of
version 1.0.1 except two that rest on the MILP:

- Finding 1: the MILP re-planner gains nothing from re-planning. In version 1.1, an autoscaler
  whose new workers do not fit around a fixed placement re-solves the placement instead of
  deferring the scale-up, so the schedule without re-planning scales as the re-planning one does
  and reaches the same completion, 0.255.
- Finding 3: on the placement-stress variant the MILP, which in version 1.1 places unanchored
  stages on edge and cloud nodes only, ties with the best strategies, so on-time delivery spreads
  the six strategies by 0.41.

## The intent-compiler paper

The intent-compiler paper (RISE-DL 2026 at IoT 2026) is reproduced from the companion repository
[continuum-intent-compiler](https://github.com/lilanpei/continuum-intent-compiler), which installs
this repository at the tag `v1.1.0` and gives the commands that reproduce the paper's results.
