# Splits

## Purpose

This directory defines benchmark train/validation/test splits for later AI-based methods.

## Key Files

- `train.yaml`: current training split entries.
- `val.yaml`: current validation split entries.
- `test.yaml`: current test split entries.

## When To Read This

- when you need a reproducible split definition for downstream AI work
- when you are changing scenario-family coverage across train, validation, and test

Current files:
- `train.yaml`
- `val.yaml`
- `test.yaml`

Each split file uses a lightweight human-readable format:

```yaml
split: train
entries:
  - scenario: eo_dual_stream
    family: earth_observation
    config: configs/eo_small.yaml
```

Field meaning:
- `scenario`: scenario name expected by the benchmark runtime.
- `family`: coarse scenario family label used for generalization and held-out-family experiments.
- `config`: repo-relative config path used to instantiate that scenario.

Design intent:
- keep the split files readable enough to review manually
- make them directly parseable by later data-pipeline or training code
- let benchmark and AI papers talk about both scenario names and broader workload families

Current split policy:
- train: EO + industrial IoT
- val: driving/V2X
- test: split inference

This is an initial benchmark-paper-friendly split, not a final AI benchmark standard.
