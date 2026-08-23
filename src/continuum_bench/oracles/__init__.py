"""Offline reduced-snapshot oracle exports.

`solve_small_instance` is the primary public entrypoint for reduced snapshots.
It supports exact enumeration and an optional SciPy/HiGHS MILP backend for
linear reduced-snapshot objectives.
"""

from continuum_bench.oracles.milp_oracle import OracleResult, solve_small_instance

__all__ = ["OracleResult", "solve_small_instance"]
