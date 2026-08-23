# Src Layout

Within `src/`, the installable package lives at `src/continuum_bench/`.

This repository uses a `src/` layout so imports stay honest during development and package metadata matches the checked-in source tree.

Main package:
- `src/continuum_bench/`

Why this exists:
- keeps imports honest during development
- avoids accidental reliance on the repository root being on `PYTHONPATH`
- matches the package metadata in `pyproject.toml`

Most users should work through:
- top-level `README.md`
- `python -m continuum_bench.cli ...`

Most developers should start in:
- `continuum_bench/README.md`: package entry points, subsystem map, and recommended reading order.
