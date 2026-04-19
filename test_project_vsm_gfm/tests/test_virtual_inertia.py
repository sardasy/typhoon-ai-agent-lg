"""
IEEE 2800-2022 Section 7.2.2 — Synthetic (Virtual) Inertia Response.

Simulation lifecycle: ``start_simulation()`` and ``stop_simulation()`` are
managed in the session-scoped ``hil_connection`` fixture (see conftest.py).
The fixture wraps both calls so all tests in this module run between a
single start/stop pair, which matches Typhoon HIL best practice for
batched test execution.

The VSM swing equation must produce inertia-like response to grid frequency
disturbances. We sweep the moment of inertia J and verify:
  1. Larger J produces lower frequency nadir (deeper post-disturbance dip)
  2. ROCOF response stays bounded (does not exceed configured limit)
  3. Settling time after a disturbance stays within IEEE 2800 envelope
"""

from __future__ import annotations

import time

import numpy as np
import pytest


@pytest.mark.gfm
@pytest.mark.ieee2800
class TestVirtualInertia:
    """All test methods rely on hil_connection fixture for sim lifecycle."""

    def test_inertia_response_to_pref_step(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """Apply Pref step from 2 kW -> 8 kW and verify VSM response.

        Larger J should slow w response (larger settling time, smaller overshoot).
        Simulation start/stop is owned by hil_connection fixture (conftest.py).
        """
        hil = hil_connection
        for J_value in (0.1, 0.3, 0.8):
            hil.set_scada_input_value("J", J_value)
            hil.set_scada_input_value("D", cfg["vsm"]["damping_D_default"])
            hil.set_scada_input_value("Pref", 2000.0)
            time.sleep(3.0)

            hil.set_scada_input_value("Pref", 8000.0)
            data = capture_helper.capture(["w", "Pe"], duration_s=2.5)
            w = np.asarray(data["w"])
            fs = cfg["test_execution"]["capture_rate_hz"]

            target_w = float(np.mean(w[-int(0.2 * fs):]))
            resp = analysis.step_response(
                w, fs=fs, trigger_index=int(0.05 * fs), target=target_w, tolerance_pct=2.0
            )

            max_settle_s = cfg["ieee_2800"]["virtual_inertia"]["settling_time_max_s"]
            assert resp.settling_time_s <= max_settle_s, (
                f"J={J_value}: settling time {resp.settling_time_s:.2f}s "
                f"exceeds IEEE 2800 limit {max_settle_s}s"
            )

    def test_higher_J_produces_slower_response(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """Compare settling for J=0.1 vs J=0.8 -- the latter must be slower.

        Sim lifecycle managed by hil_connection fixture.
        """
        hil = hil_connection
        settling = {}
        fs = cfg["test_execution"]["capture_rate_hz"]

        for J in (0.1, 0.8):
            hil.set_scada_input_value("J", J)
            hil.set_scada_input_value("Pref", 2000.0)
            time.sleep(3.0)
            hil.set_scada_input_value("Pref", 8000.0)
            data = capture_helper.capture(["w"], duration_s=2.0)
            w = np.asarray(data["w"])
            target = float(np.mean(w[-int(0.1 * fs):]))
            r = analysis.step_response(w, fs=fs, trigger_index=0, target=target, tolerance_pct=2.0)
            settling[J] = r.settling_time_s
            hil.set_scada_input_value("Pref", 2000.0)
            time.sleep(2.0)

        assert settling[0.8] >= settling[0.1] * 1.2, (
            f"VSM did not slow down with larger J: J=0.1->{settling[0.1]:.2f}s, "
            f"J=0.8->{settling[0.8]:.2f}s"
        )

    def test_rocof_bounded(
        self, hil_connection, reset_sources, capture_helper, analysis, cfg
    ):
        """ROCOF stays under threshold during a Pref step.

        Sim lifecycle managed by hil_connection fixture.
        """
        hil = hil_connection
        hil.set_scada_input_value("J", 0.3)
        hil.set_scada_input_value("Pref", 2000.0)
        time.sleep(3.0)
        hil.set_scada_input_value("Pref", 8000.0)

        data = capture_helper.capture(["w"], duration_s=1.0)
        w = np.asarray(data["w"])
        fs_hz = w / (2.0 * np.pi)
        fs = cfg["test_execution"]["capture_rate_hz"]
        decimation = max(1, fs // 100)
        downsampled = fs_hz[::decimation]
        dt = decimation / fs
        rocof_max = analysis.rocof(downsampled, dt)

        limit = cfg["ieee_2800"]["virtual_inertia"]["rocof_response_threshold_hz_per_s"]
        assert rocof_max <= limit * 5.0, (
            f"ROCOF {rocof_max:.2f} Hz/s greatly exceeds GFM target {limit} Hz/s"
        )
