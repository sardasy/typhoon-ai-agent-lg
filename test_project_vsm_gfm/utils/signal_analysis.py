"""
Signal analysis utilities for IEEE 2800 GFM compliance verification.

All functions accept numpy arrays from typhoon.test.capture.get_capture_results()
or pandas DataFrames returned by the high-level capture API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Per-unit conversions
# ---------------------------------------------------------------------------

def to_pu(value: float, base: float) -> float:
    """Convert a physical value to per-unit."""
    if base == 0:
        return 0.0
    return value / base


def from_pu(value_pu: float, base: float) -> float:
    return value_pu * base


# ---------------------------------------------------------------------------
# RMS / power
# ---------------------------------------------------------------------------

def rms(signal: np.ndarray) -> float:
    """RMS of a signal (handles 1-D and 2-D)."""
    return float(np.sqrt(np.mean(np.square(signal))))


def three_phase_rms(va: np.ndarray, vb: np.ndarray, vc: np.ndarray) -> float:
    """Total 3-phase RMS magnitude (used for Vt-like quantities)."""
    return math.sqrt(rms(va) ** 2 + rms(vb) ** 2 + rms(vc) ** 2)


def active_power(va: np.ndarray, vb: np.ndarray, vc: np.ndarray,
                 ia: np.ndarray, ib: np.ndarray, ic: np.ndarray) -> float:
    """Average 3-phase active power P = mean(va*ia + vb*ib + vc*ic)."""
    return float(np.mean(va * ia + vb * ib + vc * ic))


def reactive_power(va: np.ndarray, vb: np.ndarray, vc: np.ndarray,
                   ia: np.ndarray, ib: np.ndarray, ic: np.ndarray) -> float:
    """Approximate 3-phase reactive power via the alpha-beta transform."""
    # Clarke transform (amplitude-invariant)
    v_alpha = (2.0 / 3.0) * (va - 0.5 * vb - 0.5 * vc)
    v_beta = (2.0 / 3.0) * (math.sqrt(3) / 2 * (vb - vc))
    i_alpha = (2.0 / 3.0) * (ia - 0.5 * ib - 0.5 * ic)
    i_beta = (2.0 / 3.0) * (math.sqrt(3) / 2 * (ib - ic))
    q = (3.0 / 2.0) * (v_beta * i_alpha - v_alpha * i_beta)
    return float(np.mean(q))


# ---------------------------------------------------------------------------
# Frequency / phase tracking
# ---------------------------------------------------------------------------

def estimate_frequency(signal: np.ndarray, fs: float) -> float:
    """Zero-crossing-based frequency estimate (Hz)."""
    centered = signal - np.mean(signal)
    crossings = np.where(np.diff(np.sign(centered)) > 0)[0]
    if len(crossings) < 2:
        return 0.0
    avg_period_samples = float(np.mean(np.diff(crossings)))
    return fs / avg_period_samples


def rocof(freq_series: np.ndarray, dt: float) -> float:
    """Maximum Rate of Change of Frequency (Hz/s)."""
    if len(freq_series) < 2:
        return 0.0
    df = np.diff(freq_series) / dt
    return float(np.max(np.abs(df)))


def phase_angle_deg(signal: np.ndarray, fs: float, fundamental_hz: float) -> float:
    """Estimate phase angle of fundamental via FFT (degrees)."""
    N = len(signal)
    fft_vals = np.fft.rfft(signal)
    bin_idx = int(round(fundamental_hz * N / fs))
    if bin_idx >= len(fft_vals):
        return 0.0
    return float(np.degrees(np.angle(fft_vals[bin_idx])))


# ---------------------------------------------------------------------------
# THD
# ---------------------------------------------------------------------------

def thd(signal: np.ndarray, fs: float, fundamental_hz: float,
        max_harmonic: int = 50) -> float:
    """Total Harmonic Distortion (%) of a signal at a given fundamental."""
    N = len(signal)
    if N == 0:
        return 0.0
    fft_mag = np.abs(np.fft.rfft(signal)) * 2.0 / N
    df = fs / N
    fund_idx = int(round(fundamental_hz / df))
    if fund_idx == 0 or fund_idx >= len(fft_mag):
        return 0.0
    fundamental = fft_mag[fund_idx]
    if fundamental == 0:
        return 0.0
    sumsq = 0.0
    for h in range(2, max_harmonic + 1):
        idx = int(round(h * fundamental_hz / df))
        if idx < len(fft_mag):
            sumsq += fft_mag[idx] ** 2
    return 100.0 * math.sqrt(sumsq) / fundamental


# ---------------------------------------------------------------------------
# Response time / settling time
# ---------------------------------------------------------------------------

@dataclass
class StepResponse:
    settling_time_s: float
    overshoot_pct: float
    steady_state_value: float
    rise_time_s: float
    response_time_to_threshold_s: float | None = None


def step_response(signal: np.ndarray, fs: float, *,
                  trigger_index: int, target: float,
                  tolerance_pct: float = 2.0,
                  threshold: float | None = None) -> StepResponse:
    """Analyse a step response after ``trigger_index``.

    settling_time_s : time for |signal - target| <= tolerance for the rest of
                      the window
    overshoot_pct   : peak excursion beyond ``target`` as % of |target|
    rise_time_s     : 10% -> 90% of (steady - initial)
    response_time_to_threshold_s : optional time for signal to first cross
                                   ``threshold`` (used for FFCI tests)
    """
    dt = 1.0 / fs
    post = signal[trigger_index:]
    tail_n = max(int(0.1 * len(post)), 10)
    steady = float(np.mean(post[-tail_n:]))
    initial = float(np.mean(signal[max(0, trigger_index - tail_n):trigger_index] or [0.0]))
    delta = steady - initial
    if abs(target) < 1e-9:
        overshoot_pct = 0.0
    else:
        overshoot_pct = max(0.0, (np.max(post) - target) / abs(target) * 100.0)

    # Settling time
    tol = abs(target) * tolerance_pct / 100.0 if abs(target) > 1e-9 else tolerance_pct
    settled_at = None
    for i in range(len(post)):
        if np.all(np.abs(post[i:] - target) <= tol):
            settled_at = i * dt
            break
    settling_time_s = settled_at if settled_at is not None else float(len(post) * dt)

    # Rise time 10-90% of step
    rise_time_s = 0.0
    if abs(delta) > 1e-9:
        low = initial + 0.1 * delta
        high = initial + 0.9 * delta
        try:
            i_low = next(i for i, v in enumerate(post) if (delta > 0 and v >= low) or (delta < 0 and v <= low))
            i_high = next(i for i, v in enumerate(post) if (delta > 0 and v >= high) or (delta < 0 and v <= high))
            rise_time_s = (i_high - i_low) * dt
        except StopIteration:
            pass

    # Threshold crossing (for FFCI: I_inj exceeds 1.0 pu)
    response_time = None
    if threshold is not None:
        for i, v in enumerate(post):
            if v >= threshold:
                response_time = i * dt
                break

    return StepResponse(
        settling_time_s=settling_time_s,
        overshoot_pct=overshoot_pct,
        steady_state_value=steady,
        rise_time_s=rise_time_s,
        response_time_to_threshold_s=response_time,
    )


# ---------------------------------------------------------------------------
# Pass/fail helpers
# ---------------------------------------------------------------------------

def within_band(value: float, target: float, tolerance_pct: float) -> bool:
    if abs(target) < 1e-9:
        return abs(value) <= tolerance_pct
    return abs(value - target) <= abs(target) * tolerance_pct / 100.0
