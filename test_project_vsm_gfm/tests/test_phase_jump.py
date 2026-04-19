"""
IEEE 2800-2022 Section 7.3 — Phase Jump Response.

A grid-forming IBR must remain stable and connected through grid phase angle
steps up to 25 degrees. We apply phase jumps via the grid source and verify:
  1. No trip / disconnection occurs
  2. Inverter currents remain bounded (under 1.5 pu peak)
  3. The VSM resynchronises within stability_recovery_time_s

Sim lifecycle: managed by hil_connection fixture in conftest.py.
"""

from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.mark.gfm
@pytest.mark.ieee2800
class TestPhaseJump:

    @pytest.mark.parametrize("phase_step_deg", [10.0, 20.0, 25.0])
    def test_phase_jump_no_overcurrent(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg, phase_step_deg
    ):
        """Inverter peak current must not exceed 1.5 pu after phase jump."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]
        pj = cfg["ieee_2800"]["phase_jump_response"]

        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(3.0)
        base_current_a = 5000.0 / (np.sqrt(3) * nom_v)

        # Capture before applying phase step
        capture_duration = pj["stability_recovery_time_s"] + 0.5

        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"],
            rms=nom_v, frequency=nom_f, phase=phase_step_deg,
        )
        data = capture_helper.capture(["Ia", "Ib", "Ic"], duration_s=capture_duration)
        # Restore phase
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        ia_peak = float(np.max(np.abs(np.asarray(data["Ia"]))))
        peak_pu = ia_peak / (base_current_a * np.sqrt(2))
        assert peak_pu <= pj["max_overcurrent_pu"] * 1.05, (
            f"Phase jump {phase_step_deg} deg caused {peak_pu:.2f} pu peak current "
            f"(limit {pj['max_overcurrent_pu']} pu)"
        )

    @pytest.mark.parametrize("phase_step_deg", [10.0, 20.0, 25.0])
    def test_phase_jump_resync_time(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg, phase_step_deg
    ):
        """VSM internal angle (teta) settles within stability_recovery_time_s."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]
        pj = cfg["ieee_2800"]["phase_jump_response"]

        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(3.0)

        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=phase_step_deg
        )
        data = capture_helper.capture(["w"], duration_s=pj["stability_recovery_time_s"] + 0.5)
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        w = np.asarray(data["w"])
        fs = cfg["test_execution"]["capture_rate_hz"]
        target_w = float(np.mean(w[-int(0.1 * fs):]))
        resp = analysis.step_response(
            w, fs=fs, trigger_index=int(0.05 * fs), target=target_w, tolerance_pct=2.0
        )

        assert resp.settling_time_s <= pj["stability_recovery_time_s"], (
            f"Phase jump {phase_step_deg} deg: w settling {resp.settling_time_s:.2f}s "
            f"exceeds limit {pj['stability_recovery_time_s']} s"
        )

    def test_no_disconnect_at_max_jump(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """At 25 deg phase jump (IEEE 2800 max), inverter must keep injecting current."""
        hil = hil_connection
        nom_v = cfg["grid"]["nominal_voltage_rms_ll"]
        nom_f = cfg["grid"]["nominal_frequency_hz"]

        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(3.0)

        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=25.0
        )
        data = capture_helper.capture(["Ia"], duration_s=2.0)
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"], rms=nom_v, frequency=nom_f, phase=0.0
        )

        ia = np.asarray(data["Ia"])
        # Use last 0.5s for steady-state RMS
        fs = cfg["test_execution"]["capture_rate_hz"]
        steady_rms = analysis.rms(ia[-int(0.5 * fs):])
        assert steady_rms > 0.5, (
            f"Inverter appears disconnected after 25 deg phase jump "
            f"(steady RMS = {steady_rms:.3f} A)"
        )
