"""
IEEE 2800-2022 Section 7.5 — Harmonic Distortion / Damping.

GFM IBR must keep voltage and current THD within IEEE 2800/IEEE 519 limits
even when the grid voltage contains background distortion.

Sim lifecycle: managed by hil_connection fixture in conftest.py.
"""

from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.mark.gfm
@pytest.mark.ieee2800
class TestHarmonicDamping:

    def test_voltage_thd_clean_grid(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        hil = hil_connection
        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(2.0)

        data = capture_helper.capture(["Va"], duration_s=0.5)
        va = np.asarray(data["Va"])
        fs = cfg["test_execution"]["capture_rate_hz"]
        thd = analysis.thd(va, fs=fs, fundamental_hz=cfg["grid"]["nominal_frequency_hz"])
        assert thd <= cfg["ieee_2800"]["harmonic_damping"]["voltage_thd_max_pct"], (
            f"Va THD {thd:.2f}% exceeds limit "
            f"{cfg['ieee_2800']['harmonic_damping']['voltage_thd_max_pct']}%"
        )

    def test_current_thd_at_rated_power(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        hil = hil_connection
        hil.set_scada_input_value("Pref", 5000.0)
        time.sleep(2.0)

        data = capture_helper.capture(["Ia"], duration_s=0.5)
        ia = np.asarray(data["Ia"])
        fs = cfg["test_execution"]["capture_rate_hz"]
        thd = analysis.thd(ia, fs=fs, fundamental_hz=cfg["grid"]["nominal_frequency_hz"])
        assert thd <= cfg["ieee_2800"]["harmonic_damping"]["current_thd_max_pct"], (
            f"Ia THD {thd:.2f}% exceeds limit "
            f"{cfg['ieee_2800']['harmonic_damping']['current_thd_max_pct']}%"
        )
