from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Protocol


_MAX_DIRECT_POISSON_RATE = 500.0


class WorkloadStream(Protocol):
    """A deterministic seeded source workload interface."""

    def sample(self, epoch: int) -> int:
        """Return the workload count for one epoch."""

        ...


@dataclass(frozen=True)
class RegimeSegment:
    """A fixed-rate workload segment lasting a set number of epochs."""

    epochs: int
    rate: float


class PiecewiseRegimeWorkload:
    """Piecewise-constant Poisson workload."""

    def __init__(self, segments: List[RegimeSegment], seed: int, repeat: bool = False):
        """Create a seeded piecewise workload stream."""

        if not segments:
            raise ValueError("segments must not be empty")
        self._segments = list(segments)
        self._rng = random.Random(seed)
        self._repeat = bool(repeat)

        self._boundaries: List[int] = []
        running = 0
        for segment in self._segments:
            running += max(1, int(segment.epochs))
            self._boundaries.append(running)

    def sample(self, epoch: int) -> int:
        """Sample one epoch from the active regime."""

        idx = self._segment_index(epoch)
        rate = max(0.0, float(self._segments[idx].rate))
        return _sample_poisson(rate, self._rng)

    def _segment_index(self, epoch: int) -> int:
        """Return the segment index covering the epoch."""

        current_epoch = int(epoch)
        if self._repeat and self._boundaries:
            period = self._boundaries[-1]
            if period > 0:
                current_epoch %= period
        for idx, boundary in enumerate(self._boundaries):
            if current_epoch < boundary:
                return idx
        return len(self._boundaries) - 1


class MMPPWorkload:
    """Two-state Markov-modulated Poisson Process workload."""

    def __init__(
        self,
        low_rate: float,
        high_rate: float,
        p_low_to_high: float,
        p_high_to_low: float,
        seed: int,
        initial_state: str = "low",
    ):
        """Create a seeded two-state MMPP workload."""

        if initial_state not in {"low", "high"}:
            raise ValueError("initial_state must be 'low' or 'high'")
        self.low_rate = max(0.0, float(low_rate))
        self.high_rate = max(0.0, float(high_rate))
        self.p_low_to_high = min(1.0, max(0.0, float(p_low_to_high)))
        self.p_high_to_low = min(1.0, max(0.0, float(p_high_to_low)))
        self._state = initial_state
        self._rng = random.Random(seed)

    def sample(self, epoch: int) -> int:
        """Sample one epoch and then advance the Markov state."""

        del epoch
        rate = self.low_rate if self._state == "low" else self.high_rate
        sample = _sample_poisson(rate, self._rng)
        self._advance_state()
        return sample

    def _advance_state(self) -> None:
        """Transition between low and high regimes."""

        probability = self._rng.random()
        if self._state == "low" and probability < self.p_low_to_high:
            self._state = "high"
            return
        if self._state == "high" and probability < self.p_high_to_low:
            self._state = "low"


@dataclass(frozen=True)
class JitteredBurstSpec:
    """Periodic burst whose PERIOD is drawn once per seed (unknown to controllers)."""

    period_lo: float
    period_hi: float
    on_duration: int
    burst_rate: float
    idle_rate: float = 0.0
    onset_noise: float = 0.0  # per-burst uniform +/- shift on the onset epoch


class JitteredBurstWorkload:
    """Bursts at onsets n*period (+ optional per-burst noise); period is per-seed and constant.

    A fixed-period controller tuned to the mean period mis-aligns for off-mean seeds; an online
    forecaster can estimate THIS seed's period from observed onsets. Burst mass (burst_rate*on_duration)
    is fixed; per-seed mean rate varies only with the (modest) period spread.
    """

    def __init__(self, spec: "JitteredBurstSpec", seed: int):
        """Draw the per-seed burst period once and seed the Poisson stream."""

        if spec.period_hi < spec.period_lo or spec.period_lo <= 0:
            raise ValueError("require 0 < period_lo <= period_hi")
        self._spec = spec
        self._seed = int(seed)
        period_rng = random.Random(self._seed)            # draw the per-seed period ONCE, first
        self.period = period_rng.uniform(spec.period_lo, spec.period_hi)
        self._rng = random.Random(self._seed * 2 + 1)     # separate stream for Poisson counts

    def _onset(self, k: int) -> float:
        """Return the onset epoch of the k-th burst, with optional per-burst noise."""

        base = k * self.period
        if self._spec.onset_noise <= 0:
            return base
        r = random.Random(f"{self._seed}:{int(k)}")        # deterministic per-burst noise
        return base + r.uniform(-self._spec.onset_noise, self._spec.onset_noise)

    def _rate_at(self, epoch: int) -> float:
        """Return the arrival rate at one epoch (burst_rate inside a burst, else idle_rate)."""

        k0 = int(epoch // self.period)
        for k in (k0 - 1, k0, k0 + 1):
            onset = self._onset(k)
            if onset <= epoch < onset + int(self._spec.on_duration):
                return float(self._spec.burst_rate)
        return float(self._spec.idle_rate)

    def sample(self, epoch: int) -> int:
        """Sample one epoch's arrival count from the per-epoch rate."""

        return _sample_poisson(self._rate_at(int(epoch)), self._rng)


@dataclass(frozen=True)
class PhaseBurstSpec:
    """Fixed-period burst; the per-seed PHASE (onset offset) is drawn per seed. Holds the mean."""

    period: int
    on_duration: int
    burst_rate: float
    idle_rate: float = 0.0


class PhaseBurstWorkload:
    """Bursts of `on_duration` epochs at `burst_rate`, recurring every `period` epochs, with a
    per-seed phase offset in [0, period); `idle_rate` fills the rest so the per-period mean
    (on*burst_rate + (period-on)*idle_rate)/period is identical for every seed (only WHEN varies).
    """

    def __init__(self, spec: "PhaseBurstSpec", seed: int):
        """Draw the per-seed onset phase once and seed the Poisson stream."""

        if spec.period <= 0 or not (0 <= spec.on_duration <= spec.period):
            raise ValueError("require period>0 and 0<=on_duration<=period")
        self._spec = spec
        self._seed = int(seed)
        self.period = int(spec.period)
        self.phase = random.Random(self._seed).uniform(0.0, float(spec.period))  # per-seed onset
        self._rng = random.Random(self._seed * 2 + 1)                            # Poisson stream

    def _rate_at(self, epoch: int) -> float:
        """Return the arrival rate at one epoch (burst_rate inside a burst, else idle_rate)."""

        # distance from the most recent onset (onsets at phase + k*period)
        rel = (float(epoch) - self.phase) % float(self.period)
        return float(self._spec.burst_rate) if rel < float(self._spec.on_duration) else float(self._spec.idle_rate)

    def sample(self, epoch: int) -> int:
        """Sample one epoch's arrival count from the per-epoch rate."""

        return _sample_poisson(self._rate_at(int(epoch)), self._rng)


def build_stream(config: Dict[str, Any], seed: int) -> WorkloadStream:
    """Build a workload stream from configuration."""

    generator = str(config.get("generator", "piecewise")).lower()

    if generator in {"piecewise", "regime", "regimes"}:
        raw_segments = config.get("segments", [])
        segments = [
            RegimeSegment(
                epochs=int(segment.get("epochs", 1)),
                rate=float(segment.get("rate", 0.0)),
            )
            for segment in raw_segments
        ]
        if not segments:
            raise ValueError("piecewise workload requires non-empty 'segments'")
        return PiecewiseRegimeWorkload(
            segments=segments,
            seed=seed,
            repeat=bool(config.get("repeat", False)),
        )

    if generator == "mmpp":
        return MMPPWorkload(
            low_rate=float(config.get("low_rate", 0.5)),
            high_rate=float(config.get("high_rate", 5.0)),
            p_low_to_high=float(config.get("p_low_to_high", 0.1)),
            p_high_to_low=float(config.get("p_high_to_low", 0.3)),
            seed=seed,
            initial_state=str(config.get("initial_state", "low")),
        )

    if generator == "jittered_burst":
        return JitteredBurstWorkload(
            JitteredBurstSpec(
                period_lo=float(config.get("period_lo", 90.0)),
                period_hi=float(config.get("period_hi", 150.0)),
                on_duration=int(config.get("on_duration", 5)),
                burst_rate=float(config.get("burst_rate", 6.0)),
                idle_rate=float(config.get("idle_rate", 0.0)),
                onset_noise=float(config.get("onset_noise", 0.0)),
            ),
            seed=seed,
        )

    if generator == "phase_burst":
        return PhaseBurstWorkload(
            PhaseBurstSpec(
                period=int(config.get("period", 120)),
                on_duration=int(config.get("on_duration", 6)),
                burst_rate=float(config.get("burst_rate", 6.0)),
                idle_rate=float(config.get("idle_rate", 0.0)),
            ),
            seed=seed,
        )

    raise ValueError(f"Unsupported workload generator: {generator}")


def _sample_poisson(rate: float, rng: random.Random) -> int:
    """Sample a Poisson variate, chunking large rates."""

    if rate <= 0.0:
        return 0

    if rate > _MAX_DIRECT_POISSON_RATE:
        total = 0
        remaining = rate
        while remaining > _MAX_DIRECT_POISSON_RATE:
            total += _sample_poisson_knuth(_MAX_DIRECT_POISSON_RATE, rng)
            remaining -= _MAX_DIRECT_POISSON_RATE
        return total + _sample_poisson_knuth(remaining, rng)

    return _sample_poisson_knuth(rate, rng)


def _sample_poisson_knuth(rate: float, rng: random.Random) -> int:
    """Sample a Poisson variate with Knuth's method."""

    if rate <= 0.0:
        return 0

    limit = math.exp(-rate)
    count = 0
    product = 1.0
    while product > limit:
        count += 1
        product *= rng.random()
    return count - 1
