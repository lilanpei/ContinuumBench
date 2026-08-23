"""TDD for the fixed-period, per-seed-PHASE-jitter burst (holds the per-column mean exactly)."""

from __future__ import annotations

import continuum_bench.workload.mmpp as mmpp_module
from continuum_bench.workload.mmpp import PhaseBurstSpec, PhaseBurstWorkload, build_stream


def _spec(idle=0.5) -> PhaseBurstSpec:
    return PhaseBurstSpec(period=120, on_duration=6, burst_rate=6.0, idle_rate=idle)


def test_phase_differs_across_seeds_period_fixed():
    a = PhaseBurstWorkload(_spec(), seed=1)
    b = PhaseBurstWorkload(_spec(), seed=2)
    assert a.period == b.period == 120          # period is FIXED
    assert abs(a.phase - b.phase) > 1.0         # per-seed phase differs


def test_realized_mean_is_seed_invariant(monkeypatch):
    # expected per-epoch arrivals == rate (deterministic), so the per-period mass is identical per seed.
    monkeypatch.setattr(mmpp_module, "_sample_poisson", lambda rate, rng: rate)
    spec = _spec(idle=0.5)
    means = []
    for seed in (1, 2, 3, 4):
        w = PhaseBurstWorkload(spec, seed=seed)
        total = sum(w._rate_at(e) for e in range(spec.period * 4))
        means.append(total / (spec.period * 4))
    # mean = (on*burst + off*idle)/period, independent of phase -> identical across seeds
    assert max(means) - min(means) < 1e-9
    expected = (6 * 6.0 + (120 - 6) * 0.5) / 120
    assert abs(means[0] - expected) < 1e-9


def test_burst_present_at_phase_onset(monkeypatch):
    monkeypatch.setattr(mmpp_module, "_sample_poisson", lambda rate, rng: rate)
    w = PhaseBurstWorkload(_spec(idle=0.0), seed=5)
    onset = int(round(w.phase))
    assert w.sample(onset) > 0                      # burst fires at the per-seed phase
    assert w.sample((onset - 10) % 120) == 0        # idle away from the burst (idle_rate=0)


def test_build_stream_dispatches_phase_burst():
    w = build_stream({"generator": "phase_burst", "period": 120, "on_duration": 6,
                      "burst_rate": 6.0, "idle_rate": 0.5}, seed=7)
    assert isinstance(w, PhaseBurstWorkload)
