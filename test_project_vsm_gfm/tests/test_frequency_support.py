"""
IEEE 2800-2022 Section 7.2.1 — Primary Frequency Response (Frequency Support).

GFM IBR provides frequency support via P-f droop. We exercise the grid source
frequency and verify the VSM adjusts active power output accordingly.

Sim lifecycle: handled in conftest.py session fixture (start/stop pair).
"""

from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.mark.gfm
@pytest.mark.ieee2800
class TestFrequencySupport:

    @pytest.mark.parametrize("delta_hz", [0.1, 0.2, 0.5])
    def test_droop_response_underfrequency(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg, delta_hz
    ):
        """Pe must increase when grid frequency drops (P-f droop)."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]

        hil.set_scada_input_value("Pref", 5000.0)
        hil.set_scada_input_value("J", 0.3)
        hil.set_scada_input_value("D", 10.0)
        time.sleep(3.0)

        # Baseline Pe
        d0 = capture_helper.capture(
            ["Va", "Vb", "Vc", "Ia", "Ib", "Ic"], duration_s=0.4
        )
        p_before = analysis.active_power(
            np.asarray(d0["Va"]), np.asarray(d0["Vb"]), np.asarray(d0["Vc"]),
            np.asarray(d0["Ia"]), np.asarray(d0["Ib"]), np.asarray(d0["Ic"]),
        )

        # Drop grid frequency
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v,
            frequency=nom_f - delta_hz, phase=0.0,
        )
        time.sleep(cfg["ieee_2800"]["frequency_support"]["primary_response_time_max_s"])

        d1 = capture_helper.capture(
            ["Va", "Vb", "Vc", "Ia", "Ib", "Ic"], duration_s=0.4
        )
        p_after = analysis.active_power(
            np.asarray(d1["Va"]), np.asarray(d1["Vb"]), np.asarray(d1["Vc"]),
            np.asarray(d1["Ia"]), np.asarray(d1["Ib"]), np.asarray(d1["Ic"]),
        )

        # Restore freq
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        assert p_after > p_before, (
            f"Underfrequency Δf=-{delta_hz} Hz: P did not increase "
            f"(before={p_before:.1f} W, after={p_after:.1f} W)"
        )

    @pytest.mark.parametrize("delta_hz", [0.1, 0.2, 0.5])
    def test_droop_response_overfrequency(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg, delta_hz
    ):
        """Pe must decrease when grid frequency rises."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]

        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(3.0)

        d0 = capture_helper.capture(
            ["Va", "Vb", "Vc", "Ia", "Ib", "Ic"], duration_s=0.4
        )
        p_before = analysis.active_power(
            np.asarray(d0["Va"]), np.asarray(d0["Vb"]), np.asarray(d0["Vc"]),
            np.asarray(d0["Ia"]), np.asarray(d0["Ib"]), np.asarray(d0["Ic"]),
        )

        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v,
            frequency=nom_f + delta_hz, phase=0.0,
        )
        time.sleep(cfg["ieee_2800"]["frequency_support"]["primary_response_time_max_s"])

        d1 = capture_helper.capture(
            ["Va", "Vb", "Vc", "Ia", "Ib", "Ic"], duration_s=0.4
        )
        p_after = analysis.active_power(
            np.asarray(d1["Va"]), np.asarray(d1["Vb"]), np.asarray(d1["Vc"]),
            np.asarray(d1["Ia"]), np.asarray(d1["Ib"]), np.asarray(d1["Ic"]),
        )

        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        assert p_after < p_before, (
            f"Overfrequency Δf=+{delta_hz} Hz: P did not decrease "
            f"(before={p_before:.1f} W, after={p_after:.1f} W)"
        )

    def test_primary_response_time(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """Pe must reach 90% of new operating point within primary response window."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]
        max_t = cfg["ieee_2800"]["frequency_support"]["primary_response_time_max_s"]

        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(3.0)

        # Apply -0.5 Hz step then capture power probe Pe directly
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f - 0.5, phase=0.0
        )
        data = capture_helper.capture(["Pe"], duration_s=max_t + 1.0)
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        pe = np.asarray(data["Pe"])
        fs = cfg["test_execution"]["capture_rate_hz"]
        target = float(np.mean(pe[-int(0.2 * fs):]))
        resp = analysis.step_response(
            pe, fs=fs, trigger_index=int(0.05 * fs), target=target, tolerance_pct=10.0
        )
        assert resp.settling_time_s <= max_t, (
            f"Primary response settling {resp.settling_time_s:.2f}s exceeds "
            f"IEEE 2800 limit {max_t}s"
        )
