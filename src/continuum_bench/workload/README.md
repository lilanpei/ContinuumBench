# Workload Generators

## Purpose

This directory contains the deterministic workload generators used by scenario source stages.

## Key Files

- `mmpp.py`: piecewise-regime and two-state MMPP generators plus the `build_stream` entry point.
- `__init__.py`: package exports for workload helpers.

## When To Read This

- when you are changing source-arrival behavior or adding a new workload generator
- when you need to understand how scenario workload configs map onto seeded runtime streams

Piecewise workloads normally hold the final segment after the configured segments end. For paper sweeps with longer horizons, set `workload.repeat: true` at the manifest or config level to repeat finite regime patterns instead. The runner applies this global flag to every piecewise source stream before building the seeded generators.
