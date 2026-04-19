"""
Pure-Python waveform analytics used by hil_tools._capture to populate
WaveformStats derived fields (rms, rise/settling/overshoot, thd, rocof).

Keeps hil_tools.py lean and lets the evaluator rely on these fields instead
of each rule reimplementing FFT / step-response detection.

All functions accept a ``Sequence[float]`` + sample rate in Hz and are pure.
Gracefully returns ``None`` when not enough samples are available rather
than raising -- the evaluator treats ``None`` as "capture lacked the data".
"""

from __future__ import annotations

import math
from typing import Sequence


# ---------------------------------------------------------------------------
# Basic stats
# ---------------------------------------------------------------------------

def mean(data: Sequence[float]) -> float:
    return sum(data) / len(data) if data else 0.0


def rms(data: Sequence[float]) -> float:
    if not data:
        return 0.0
    return math.sqrt(sum(x * x for x in data) / len(data))


# ---------------------------------------------------------------------------
# Step response metrics
# ---------------------------------------------------------------------------

def rise_time_ms(
    data: Sequence[float],
    sample_rate_hz: float,
    *,
    low_fraction: float = 0.1,
    high_fraction: float = 0.9,
) -> float | None:
    """10%->90% rise time. Returns None if fewer than 4 samples or no step."""
    if len(data) < 4 or sample_rate_hz <= 0:
        return None
    baseline = min(data)
    steady = max(data)
    delta = steady - baseline
    if abs(delta) < 1e-9:
        return None
    low = baseline + low_fraction * delta
    high = baseline + high_fraction * delta
    dt_ms = 1000.0 / sample_rate_hz
    i_low = i_high = None
    for i, v in enumerate(data):
        if i_low is None and v >= low:
            i_low = i
        if i_low is not None and v >= high:
            i_high = i
            break
    if i_low is None or i_high is None:
        return None
    return (i_high - i_low) * dt_ms


def settling_time_ms(
    data: Sequence[float],
    sample_rate_hz: float,
    *,
    tolerance_pct: float = 2.0,
) -> float | None:
    """Earliest time from which |data - steady| stays <= tolerance_pct.

    Returns None if signal never settles or not enough samples.
    """
    if len(data) < 4 or sample_rate_hz <= 0:
        return None
    tail = max(int(0.1 * len(data)), 3)
    steady = sum(data[-tail:]) / tail
    tol = abs(steady) * tolerance_pct / 100.0 if abs(steady) > 1e-9 else tolerance_pct
    dt_ms = 1000.0 / sample_rate_hz
    settled_at = None
    for i in range(len(data)):
        if all(abs(v - steady) <= tol for v in data[i:]):
            settled_at = i
            break
    if settled_at is None:
        return None
    return settled_at * dt_ms


def overshoot_percent(
    data: Sequence[float],
    *,
    target: float | None = None,
) -> float | None:
    """Peak excursion beyond target as % of |target|. None if no overshoot."""
    if not data:
        return None
    if target is None:
        # Use steady-state as target
        tail = max(int(0.1 * len(data)), 3)
        target = sum(data[-tail:]) / tail
    if abs(target) < 1e-9:
        return None
    peak = max(data) if target >= 0 else min(data)
    overshoot = (peak - target) / abs(target) * 100.0
    return overshoot if overshoot > 0 else None


# ---------------------------------------------------------------------------
# Frequency-domain metrics
# ---------------------------------------------------------------------------

def thd_percent(
    data: Sequence[float],
    sample_rate_hz: float,
    fundamental_hz: float,
    *,
    max_harmonic: int = 50,
    min_samples_per_cycle: int = 10,
) -> float | None:
    """Total Harmonic Distortion (%).

    Uses numpy FFT if available. Returns None when:
      - numpy missing
      - sample count below one full cycle OR below ``min_samples_per_cycle``
      - fundamental bin outside FFT range
    """
    try:
        import numpy as np
    except ImportError:
        return None

    N = len(data)
    if N < 8 or sample_rate_hz <= 0 or fundamental_hz <= 0:
        return None

    samples_per_cycle = sample_rate_hz / fundamental_hz
    if samples_per_cycle < min_samples_per_cycle:
        return None

    arr = np.asarray(data, dtype=float)
    # Detrend to remove DC offset
    arr = arr - arr.mean()
    mag = np.abs(np.fft.rfft(arr)) * 2.0 / N
    df = sample_rate_hz / N
    fund_idx = int(round(fundamental_hz / df))
    if fund_idx == 0 or fund_idx >= len(mag):
        return None
    fundamental = mag[fund_idx]
    if fundamental < 1e-9:
        return None
    sumsq = 0.0
    for h in range(2, max_harmonic + 1):
        idx = int(round(h * fundamental_hz / df))
        if idx < len(mag):
            sumsq += mag[idx] ** 2
    return 100.0 * math.sqrt(sumsq) / fundamental


# ---------------------------------------------------------------------------
# Frequency tracking / ROCOF
# ---------------------------------------------------------------------------

def rocof_hz_per_s(
    data: Sequence[float],
    sample_rate_hz: float,
    *,
    is_omega: bool = True,
) -> float | None:
    """Max |df/dt| across the series.

    When ``is_omega`` (default), treats samples as angular frequency (rad/s)
    and converts to Hz via f = w / (2 pi). Otherwise treats samples as Hz.
    """
    if len(data) < 2 or sample_rate_hz <= 0:
        return None
    freqs = [x / (2.0 * math.pi) for x in data] if is_omega else list(data)
    dt = 1.0 / sample_rate_hz
    derivs = [abs(freqs[i + 1] - freqs[i]) / dt for i in range(len(freqs) - 1)]
    return max(derivs) if derivs else None
