# Sweep Plumbing

## Purpose

This directory expands YAML sweep manifests into deterministic benchmark runs and preserves rendered configs for provenance.

## Key Files

- `manifest.py`: loads manifests, resolves config paths, deep-merges overrides, and expands run specifications.
- `runner.py`: stages rendered configs, invokes benchmark runs, and copies the rendered config into each run directory.
- `__init__.py`: package exports for sweep helpers.

## When To Read This

- when you are adding new manifest features or debugging override expansion
- when you are changing how sweep runs stage configs or preserve provenance
