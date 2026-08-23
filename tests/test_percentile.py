"""Unit tests for the latency-percentile estimator underlying every reported p50/p95/p99 value.

A silent switch from linear interpolation (NumPy type-7) to nearest-rank would shift every reported
percentile, so the estimator's load-bearing branches are pinned directly here.
"""

from __future__ import annotations

import pytest

from continuum_bench.metrics.summary import _percentile


def test_percentile_empty_returns_zero() -> None:
    assert _percentile([], 50.0) == 0.0
    assert _percentile([], 95.0) == 0.0


def test_percentile_single_element_returns_that_element() -> None:
    for percentile in (50.0, 95.0, 99.0):
        assert _percentile([5.0], percentile) == 5.0


def test_percentile_linear_interpolation() -> None:
    values = [0.0, 1.0, 2.0, 3.0]
    assert _percentile(values, 0.0) == pytest.approx(0.0)
    assert _percentile(values, 50.0) == pytest.approx(1.5)
    assert _percentile(values, 95.0) == pytest.approx(2.85)
    assert _percentile(values, 100.0) == pytest.approx(3.0)


def test_percentile_interpolates_rather_than_nearest_rank() -> None:
    # p99 of a two-element list must interpolate (0.99), not snap to the nearest rank (1.0).
    assert _percentile([0.0, 1.0], 99.0) == pytest.approx(0.99)


def test_percentile_is_order_independent() -> None:
    assert _percentile([3.0, 0.0, 2.0, 1.0], 50.0) == pytest.approx(1.5)
