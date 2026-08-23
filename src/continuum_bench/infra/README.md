# Infrastructure Helpers

## Purpose

This directory builds Eclypse infrastructure graphs and attaches benchmark update-policy adapters used by the runner.

## Key Files

- `generators.py`: builds the cloud, edge, IoT, and space topology as an Eclypse `Infrastructure` with node/link assets for compute, placement layers, latency, bandwidth, node availability metadata, energy, and battery metadata.
- `contact_schedule.py`: models periodic, explicit, or trace-backed contact windows as runtime contact attributes on Eclypse edges.
- `failure_policy.py`: applies benchmark-selected node failures through Eclypse node update policies.
- `__init__.py`: public exports used by the runner and scenario builders.

Boundary:
- Eclypse owns the graph, assets, node availability views, path lookups, path-resource aggregation, and cache invalidation.
- ContinuumBench owns scenario defaults, calibration-profile selection, contact-trace selection, temporary contact semantics through `contact_open`/`contact_bandwidth`/`contact_latency`, and stochastic node-failure generation.

Trace-backed contact schedules are finite by default. Paper sweeps that run beyond a trace file's final timestamp set `infrastructure.contact_schedule.repeat: true`, which replays the trace pattern instead of leaving links permanently closed after the trace ends.

## When To Read This

- when you are changing topology defaults or continuum tier counts
- when you are changing how intermittent links become available over time
