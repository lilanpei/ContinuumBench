"""Workload package."""

from continuum_bench.workload.mmpp import (
    JitteredBurstSpec,
    JitteredBurstWorkload,
    MMPPWorkload,
    PhaseBurstSpec,
    PhaseBurstWorkload,
    PiecewiseRegimeWorkload,
    RegimeSegment,
    WorkloadStream,
    build_stream,
)

__all__ = [
    "JitteredBurstSpec",
    "JitteredBurstWorkload",
    "MMPPWorkload",
    "PhaseBurstSpec",
    "PhaseBurstWorkload",
    "PiecewiseRegimeWorkload",
    "RegimeSegment",
    "WorkloadStream",
    "build_stream",
]
