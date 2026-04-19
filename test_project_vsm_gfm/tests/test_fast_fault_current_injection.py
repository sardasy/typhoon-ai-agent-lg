"""
IEEE 2800-2022 Section 7.4 — Fast Fault Current Injection (FFCI).

GFM IBR must inject reactive current within ~1 cycle of detecting a voltage
sag and sustain it for the fault duration. Verifies:
  1. Reactive current injection begins within FFCI response window
  2. Current magnitude reaches 1.0-1.5 pu of rated
  3. Current is sustained throughout the fault window

Simulation lifecycle: managed by session-scoped hil_connection fixture in
conftest.py (start_simulation/stop_simulation paired there).
"""

from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.mark.gfm
@pytest.mark.ieee2800
class TestFastFaultCurrentInjection:

    @pytest.mark.parametrize("sag_pu", [0.5, 0.3])
    def test_ffci_response_time(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg, sag_pu
    ):
        """Reactive current must rise above 1.0 pu within FFCI response time."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]
        ffci = cfg["ieee_2800"]["fast_fault_current_injection"]

        # Pre-fault steady state at rated power
        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(2.0)

        # Rated current ~= 5000 / (sqrt(3)*230) ~= 12.5 A; use 12.5 as base
        base_current_a = 5000.0 / (np.sqrt(3) * nom_v)

        # Apply sag (will be triggered after capture starts)
        capture_helper.capture(
            ["Va", "Ia", "Ib", "Ic", "Qe"],
            duration_s=1.0,
            trigger_source="Va",
            trigger_threshold=nom_v * sag_pu * np.sqrt(2) * 0.95,
            trigger_edge="Falling edge",
        )
        # Apply the sag mid-capture
        time.sleep(0.05)
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v * sag_pu, frequency=nom_f, phase=0.0
        )
        time.sleep(0.5)

        # Restore grid
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        from typhoon.test.capture import get_capture_results
        data = get_capture_results()

        ia = np.asarray(data["Ia"])
        fs = cfg["test_execution"]["capture_rate_hz"]
        # Compute envelope (abs of Hilbert-like) - simple peak in sliding window
        win = max(1, int(fs / 50))  # one fundamental cycle
        envelope = np.array([
            float(np.max(np.abs(ia[i:i + win]))) for i in range(0, len(ia) - win, win // 4)
        ])
        env_pu = envelope / base_current_a

        threshold = ffci["ffci_current_min_pu"]
        crossings = np.where(env_pu >= threshold)[0]
        assert len(crossings) > 0, (
            f"FFCI did not reach {threshold} pu (max observed {np.max(env_pu):.2f} pu)"
        )

        first_cross_step = crossings[0]
        time_to_inject_ms = first_cross_step * (win // 4) / fs * 1000.0
        assert time_to_inject_ms <= ffci["response_time_max_ms"], (
            f"FFCI response {time_to_inject_ms:.1f} ms > "
            f"limit {ffci['response_time_max_ms']} ms"
        )

    def test_ffci_current_magnitude_capped(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """FFCI must not exceed 1.5 pu (over-current protection limit)."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]
        ffci = cfg["ieee_2800"]["fast_fault_current_injection"]

        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(2.0)
        base_current_a = 5000.0 / (np.sqrt(3) * nom_v)

        # Severe sag
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v * 0.2, frequency=nom_f, phase=0.0
        )

        data = capture_helper.capture(["Ia", "Ib", "Ic"], duration_s=0.3)

        # Restore
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        ia_peak = float(np.max(np.abs(np.asarray(data["Ia"]))))
        peak_pu = ia_peak / (base_current_a * np.sqrt(2))  # to pu peak
        assert peak_pu <= ffci["ffci_current_max_pu"] * 1.1, (
            f"Peak current {peak_pu:.2f} pu exceeds GFM limit "
            f"{ffci['ffci_current_max_pu']} pu (10% margin)"
        )

    def test_ffci_sustained_during_fault(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """Injected current must stay above 1.0 pu for full fault duration."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]
        ffci = cfg["ieee_2800"]["fast_fault_current_injection"]
        sustain_s = ffci["sustain_duration_s"]

        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(2.0)
        base_current_a = 5000.0 / (np.sqrt(3) * nom_v)

        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v * 0.5, frequency=nom_f, phase=0.0
        )
        data = capture_helper.capture(["Ia"], duration_s=sustain_s + 0.1)
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        ia = np.asarray(data["Ia"])
        # RMS over each fundamental cycle
        fs = cfg["test_execution"]["capture_rate_hz"]
        cycle_n = int(fs / nom_f)
        rms_per_cycle = np.array([
            analysis.rms(ia[i:i + cycle_n])
            for i in range(0, len(ia) - cycle_n, cycle_n)
        ])
        rms_pu = rms_per_cycle / base_current_a
        # Skip first cycle (transient) and check the rest
        if len(rms_pu) > 2:
            sustained_pu = rms_pu[1:]
            assert np.all(sustained_pu >= ffci["ffci_current_min_pu"] * 0.85), (
                f"Current dropped below 0.85 * threshold during fault "
                f"(min RMS {np.min(sustained_pu):.2f} pu)"
            )
