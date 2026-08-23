# Docs

## Purpose

This directory is reserved for longer-form project documentation that does not fit well in the root or package READMEs.

## Key Files

- `README.md`: the current index for what should live in `docs/` and where to look today.

## When To Read This

- when the root README is no longer detailed enough for the question you are answering
- when you are about to add a design note, contributor guide, or other longer-form document

Current status: the public repo is still lightweight, so most reader guidance lives in the targeted READMEs beside the code and configs.

Current canonical docs:
- root `README.md` for installation, benchmark scope, controllers, outputs, and the Eclypse/ContinuumBench boundary
- `src/continuum_bench/README.md` plus subsystem READMEs for implementation ownership
- `configs/README.md`, `sweeps/README.md`, `analysis/README.md`, and `tests/README.md` for runnable protocols, aggregation, and verification
- `calibration/README.md` and `src/continuum_bench/calibration/README.md` for calibration assets, packaged contact traces, and runtime contact semantics

Only tracked, linked documents in this index are project truth. If you keep local design notes or paper drafts under `docs/`, treat them as scratch material and check the tracked READMEs and live configs before relying on them.
