# Runtime Helpers

## Purpose

This directory contains the buffering and skeleton-service helpers that model benchmark runtime behavior around intermittent connectivity.

## Key Files

- `store_forward.py`: priority-aware store-and-forward buffer with TTL, overflow handling, and cumulative stats.
- `skeleton.py`: generic source, transform, and sink service helpers layered on top of the buffer and optional Eclypse services.
- `__init__.py`: package exports for runtime helpers.

Boundary:
- Eclypse provides service abstractions, route costs, placement, and substrate paths.
- ContinuumBench models benchmark-specific queues, buffer TTL/drop behavior, and link-flush semantics that are not Eclypse substrate primitives.

## When To Read This

- when you are changing buffering semantics, queue priorities, or link-flush behavior
- when you need to understand the lightweight runtime abstraction used by tests and benchmark plumbing
