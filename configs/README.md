# Configs

## Purpose

This directory contains public benchmark configurations used for smoke runs, examples, and experiment suites.

## Key Files

- `eo_small.yaml`, `driving_small.yaml`, `split_inference_small.yaml`, `industrial_iot_small.yaml`: small synthetic configs for single-run examples and smoke coverage.
- `eo_trace_small.yaml`, `driving_trace_small.yaml`, `split_inference_trace_small.yaml`, `industrial_iot_trace_small.yaml`: public trace-backed variants for the same scenario families.
- `README.md`: quick index for which config family to use and how configs relate to sweeps and splits.

## When To Read This

- when you need a config for a single benchmark run or a new smoke test
- when you are wiring configs into `splits/` or `sweeps/`

Files:
- `eo_small.yaml`: Earth-observation dual-stream workflow with intermittent contact windows and a scalable bulk path.
- `driving_small.yaml`: V2X/driving workflow with offload-sensitive execution and edge/cloud placement tradeoffs.
- `split_inference_small.yaml`: Split-inference workflow with an edge-complete fast path and a cloud-refinement path.
- `industrial_iot_small.yaml`: Industrial IoT workflow with a control path and a bulk/archive-style path under the single-sink execution model.
- `eo_trace_small.yaml`: EO trace-backed experiment config.
- `driving_trace_small.yaml`: driving/V2X trace-backed experiment config.
- `split_inference_trace_small.yaml`: split-inference trace-backed experiment config.
- `industrial_iot_trace_small.yaml`: industrial IoT trace-backed experiment config.

How to use:
- Run a single config with `python -m continuum_bench.cli run --config <config> --controller <name> --seed <n> --out runs/`.
- Use these configs directly in `splits/*.yaml` and `sweeps/*.yaml`.

Guidance:
- These are experiment defaults, not exhaustive workload definitions.
- Keep new public configs moderate and reproducible. Avoid making them pathological by default.
- If a config exists mainly for one experiment suite, prefer referencing it from `sweeps/` rather than overloading the default examples.
- In single configs, `epochs` is the source-arrival window. Sweeps may add `drain_epochs` so existing work can finish without extending the arrival process.
- Use the evaluation-regime framing for analysis: deterministic synthetic (`F0`), contact/mobility-aware (`F1`), and calibrated trace-backed (`F2`). Existing configs may still use the legacy `realism_tier` field; `T0/T1/T2` map to `F0` with energy or failure overlays when configured, `T3` maps to `F1`, and `T4` maps to `F2`.
- For temporary connectivity, use `infrastructure.contact_schedule` with `contact_open` trace rows. Do not use edge availability or link-failure fields for contact windows; placement stays structural while transport and buffers honor the runtime contact state.
