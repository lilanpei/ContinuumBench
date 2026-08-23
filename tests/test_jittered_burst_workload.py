"""TDD for the per-seed-unknown-period jittered burst workload."""

from __future__ import annotations

import continuum_bench.workload.mmpp as mmpp_module
from continuum_bench.workload.mmpp import JitteredBurstSpec, JitteredBurstWorkload, build_stream


def _spec() -> JitteredBurstSpec:
    return JitteredBurstSpec(period_lo=90.0, period_hi=150.0, on_duration=5,
                             burst_rate=6.0, idle_rate=0.0, onset_noise=0.0)


def _onsets(w: JitteredBurstWorkload, horizon: int, monkeypatch) -> list[int]:
    # deterministic arrivals: sample == int(rate); bursts are the nonzero epochs.
    monkeypatch.setattr(mmpp_module, "_sample_poisson", lambda rate, rng: int(rate))
    nz = [e for e in range(horizon) if w.sample(e) > 0]
    # collapse consecutive burst epochs into onset epochs (start of each run of nonzeros)
    onsets = [e for i, e in enumerate(nz) if i == 0 or nz[i - 1] != e - 1]
    return onsets


def test_period_is_per_seed_and_differs_across_seeds(monkeypatch):
    a = JitteredBurstWorkload(_spec(), seed=1)
    b = JitteredBurstWorkload(_spec(), seed=2)
    assert 90.0 <= a.period <= 150.0 and 90.0 <= b.period <= 150.0
    assert abs(a.period - b.period) > 1.0  # different seeds -> different periods


def test_bursts_recur_at_the_seed_period(monkeypatch):
    w = JitteredBurstWorkload(_spec(), seed=1)
    onsets = _onsets(w, horizon=700, monkeypatch=monkeypatch)
    assert len(onsets) >= 4
    gaps = [onsets[i + 1] - onsets[i] for i in range(len(onsets) - 1)]
    # with zero onset noise, every gap equals round(period)
    assert max(gaps) - min(gaps) <= 1
    assert abs(sum(gaps) / len(gaps) - w.period) <= 1.5


def test_idle_epochs_have_zero_rate(monkeypatch):
    monkeypatch.setattr(mmpp_module, "_sample_poisson", lambda rate, rng: int(rate))
    w = JitteredBurstWorkload(_spec(), seed=3)
    # an epoch far from any onset (just before the first onset, if period>10) is idle
    first = _onsets(w, 400, monkeypatch)[0]
    if first >= 10:
        assert w.sample(first - 8) == 0


def test_build_stream_dispatches_jittered_burst(monkeypatch):
    w = build_stream({"generator": "jittered_burst", "period_lo": 90, "period_hi": 150,
                      "on_duration": 5, "burst_rate": 6.0}, seed=7)
    assert isinstance(w, JitteredBurstWorkload)


def test_onset_noise_does_not_crash_and_is_deterministic(monkeypatch):
    # onset_noise > 0 must not raise (regression: random.Random rejects a tuple seed in 3.11)
    monkeypatch.setattr(mmpp_module, "_sample_poisson", lambda rate, rng: int(rate))
    spec = JitteredBurstSpec(period_lo=120.0, period_hi=120.0, on_duration=5,
                             burst_rate=6.0, idle_rate=0.0, onset_noise=8.0)
    a = JitteredBurstWorkload(spec, seed=5)
    onsets_a = _onsets(a, 360, monkeypatch)
    assert onsets_a  # bursts exist, no crash
    # deterministic for a fixed seed
    b = JitteredBurstWorkload(spec, seed=5)
    assert _onsets(b, 360, monkeypatch) == onsets_a
