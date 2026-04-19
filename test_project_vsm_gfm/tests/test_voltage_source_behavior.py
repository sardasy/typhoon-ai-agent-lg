"""
IEEE 2800-2022 Section 9 — Voltage Source Behavior of GFM IBR.

A grid-forming inverter must behave as a controllable voltage source behind
a defined impedance. Verifies:
  1. Internal voltage magnitude tracks Vref under steady state
  2. Phase angle remains controllable via VSM swing equation
  3. Voltage maintained during grid impedance step (no collapse)
"""

from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.mark.gfm
@pytest.mark.ieee2800
class TestVoltageSourceBehavior:

    def test_voltage_magnitude_steady_state(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """Va RMS within 5% of nominal at zero power export."""
        hil = hil_connection
        nom_rms = cfg["grid"]["nominal_voltage_rms_ll"]

        hil.set_scada_input_value("Pref", 0.0)
        hil.set_scada_input_value("Qref", 0.0)
        time.sleep(2.0)

        data = capture_helper.capture(["Va", "Vb", "Vc"], duration_s=0.5)
        va_rms = analysis.rms(np.asarray(data["Va"]))
        vb_rms = analysis.rms(np.asarray(data["Vb"]))
        vc_rms = analysis.rms(np.asarray(data["Vc"]))

        for ph, val in (("Va", va_rms), ("Vb", vb_rms), ("Vc", vc_rms)):
            assert analysis.within_band(val, nom_rms, 5.0), (
                f"{ph} RMS {val:.2f} V outside +/-5% of nominal {nom_rms} V"
            )

    @pytest.mark.parametrize("p_ref_w", [2000.0, 5000.0, 8000.0])
    def test_active_power_tracking(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg, p_ref_w
    ):
        """Pe tracks Pref within 10% in steady state."""
        hil = hil_connection
        hil.set_scada_input_value("Pref", p_ref_w)
        hil.set_scada_input_value("Qref", 0.0)
        time.sleep(3.0)

        data = capture_helper.capture(["Va", "Vb", "Vc", "Ia", "Ib", "Ic"], duration_s=0.5)
        p_meas = analysis.active_power(
            np.asarray(data["Va"]), np.asarray(data["Vb"]), np.asarray(data["Vc"]),
            np.asarray(data["Ia"]), np.asarray(data["Ib"]), np.asarray(data["Ic"]),
        )

        assert analysis.within_band(p_meas, p_ref_w, 10.0), (
            f"P measured {p_meas:.1f} W vs Pref {p_ref_w:.1f} W (>10% error)"
        )

    def test_voltage_holds_during_grid_impedance_step(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """Va does not collapse below 0.85 pu when grid voltage drops to 0.9 pu.

        Verifies the voltage-source behavior: GFM must hold its terminal even
        when the stiff grid is weakened.
        """
        hil = hil_connection
        nom = cfg["grid"]["nominal_voltage_rms_ll"]

        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(2.0)

        # Step grid down to 0.9 pu
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"],
            rms=nom * 0.9,
            frequency=cfg["grid"]["nominal_frequency_hz"],
            phase=0.0,
        )
        time.sleep(1.0)

        data = capture_helper.capture(["Va"], duration_s=0.5)
        va_rms_pu = analysis.rms(np.asarray(data["Va"])) / nom
        assert va_rms_pu >= 0.85, (
            f"Va collapsed to {va_rms_pu:.3f} pu under weak-grid conditions"
        )
