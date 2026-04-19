"""Unit tests for src/waveform_analytics.py"""

from __future__ import annotations

import math

import pytest

from src import waveform_analytics as wa


class TestBasic:
    def test_mean(self):
        assert wa.mean([1.0, 2.0, 3.0]) == 2.0
        assert wa.mean([]) == 0.0

    def test_rms(self):
        assert math.isclose(wa.rms([3.0, 4.0]), math.sqrt((9 + 16) / 2))
        assert wa.rms([]) == 0.0


class TestStepResponse:
    def test_rise_time_simple(self):
        # 100 samples at 1 kHz, step from 0 to 1 at sample 10
        data = [0.0] * 10 + [1.0] * 90
        t = wa.rise_time_ms(data, 1000.0)
        # Both 10% and 90% are in the final steady region, so rise is ~0 ms
        assert t is not None
        assert t <= 1.0  # one sample tick

    def test_rise_time_gradual(self):
        # Gradual 0->1 over 100 samples at 1 kHz -> 0.1 s
        data = [i / 100.0 for i in range(100)]
        t = wa.rise_time_ms(data, 1000.0)
        assert t is not None
        # 10% -> 90% over 80 samples @ 1 ms = 80 ms
        assert 70 <= t <= 90

    def test_rise_time_insufficient_samples(self):
        assert wa.rise_time_ms([1.0, 2.0], 1000.0) is None

    def test_rise_time_no_step(self):
        assert wa.rise_time_ms([5.0] * 10, 1000.0) is None

    def test_settling_time_clean(self):
        # 50 samples at 1 kHz, settles immediately at sample 20
        data = [0.0] * 20 + [1.0] * 30
        t = wa.settling_time_ms(data, 1000.0, tolerance_pct=5.0)
        assert t is not None
        assert t >= 19.0  # settled from sample 20 ~ 20 ms

    def test_overshoot_positive(self):
        data = [0.0] * 5 + [1.2] * 3 + [1.0] * 10  # 20% overshoot
        o = wa.overshoot_percent(data)
        assert o is not None and o > 15.0

    def test_overshoot_none_when_no_peak(self):
        assert wa.overshoot_percent([1.0] * 10) is None


class TestTHD:
    def test_thd_pure_sine(self):
        # 10 full cycles of 50Hz at 10 kHz sample rate -> clean sine
        fs = 10_000.0
        f = 50.0
        N = int(fs * (10 / f))  # 10 cycles
        data = [math.sin(2 * math.pi * f * i / fs) for i in range(N)]
        thd = wa.thd_percent(data, fs, f)
        # Discrete FFT of finite window has some leakage; accept <2%
        assert thd is not None and thd < 2.0

    def test_thd_with_harmonic(self):
        fs = 10_000.0
        f = 50.0
        N = int(fs * 5 / f)
        data = [
            math.sin(2 * math.pi * f * i / fs)
            + 0.10 * math.sin(2 * math.pi * 3 * f * i / fs)  # 10% 3rd harmonic
            for i in range(N)
        ]
        thd = wa.thd_percent(data, fs, f)
        assert thd is not None
        assert 8.0 <= thd <= 12.0

    def test_thd_insufficient_samples(self):
        # Only 3 samples per cycle -> below min_samples_per_cycle
        fs = 150.0
        f = 50.0
        data = [math.sin(2 * math.pi * f * i / fs) for i in range(20)]
        assert wa.thd_percent(data, fs, f) is None

    def test_thd_zero_signal(self):
        fs = 10_000.0
        f = 50.0
        N = int(fs * 5 / f)
        assert wa.thd_percent([0.0] * N, fs, f) is None


class TestROCOF:
    def test_rocof_constant_frequency(self):
        # Omega constant at 2 pi * 50 rad/s -> f=50Hz flat -> ROCOF=0
        omega = [2 * math.pi * 50] * 20
        assert wa.rocof_hz_per_s(omega, 100.0) == 0.0

    def test_rocof_linear_ramp(self):
        # Omega ramps 2 pi * 50 -> 2 pi * 52 over 1 second at 100 Hz
        fs = 100.0
        N = int(fs)
        omega = [
            2 * math.pi * (50.0 + 2.0 * i / N) for i in range(N)
        ]
        r = wa.rocof_hz_per_s(omega, fs)
        assert r is not None
        # df/dt = 2 Hz per second
        assert 1.5 <= r <= 2.5

    def test_rocof_hz_input(self):
        freqs = [50.0, 50.5, 51.0, 51.5]
        r = wa.rocof_hz_per_s(freqs, 1.0, is_omega=False)
        assert r == pytest.approx(0.5)

    def test_rocof_insufficient_samples(self):
        assert wa.rocof_hz_per_s([1.0], 100.0) is None
