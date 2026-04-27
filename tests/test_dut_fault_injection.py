"""Tests for the DUTInterface fault injection extension.

Mirim Syscon ``conftest.py`` defines six fault-injection methods on
the abstract DUT:

  inject_overvoltage / inject_undervoltage / inject_overcurrent /
  inject_source_loss / inject_sensor_fault
  expect_trip / is_tripped / clear_fault

We added them as concrete defaults on ``BaseBackend`` so every
existing backend (HIL / XCP / Hybrid / Mock) inherits the same
surface for free. This file exercises that uniform behavior on
``MockBackend``.
"""

from __future__ import annotations

import pytest

from src.tools.dut import MockBackend


pytestmark = pytest.mark.fault_injection


# ---------------------------------------------------------------------------
# Stimulus injectors (write_signal under the hood)
# ---------------------------------------------------------------------------

class TestStimulusInjection:
    @pytest.mark.asyncio
    async def test_inject_overvoltage_records_ramp(self):
        b = MockBackend()
        await b.inject_overvoltage(level_v=4.3, ramp_time_s=0.5,
                                     target="V_cell_1")
        # The ramp landed as a write_signal call with the right shape.
        recorded = [c for c in b.calls if c[0] == "write_signal"]
        assert recorded
        kwargs = recorded[-1][1]
        assert kwargs["signal"] == "V_cell_1"
        assert kwargs["waveform"] == "ramp"
        assert kwargs["end_value"] == 4.3
        assert kwargs["duration_s"] == 0.5

    @pytest.mark.asyncio
    async def test_inject_undervoltage_default_target_vsa(self):
        b = MockBackend()
        await b.inject_undervoltage(level_v=160.0)
        kwargs = b.calls[-1][1]
        assert kwargs["signal"] == "Vsa"
        assert kwargs["end_value"] == 160.0
        assert kwargs["start_value"] == pytest.approx(325.27)

    @pytest.mark.asyncio
    async def test_inject_overcurrent_writes_setpoint(self):
        b = MockBackend()
        await b.inject_overcurrent(target_a=50.0)
        kwargs = b.calls[-1][1]
        assert kwargs["signal"] == "load_resistance"
        assert kwargs["value"] == 50.0

    @pytest.mark.asyncio
    async def test_inject_source_loss_zeros_target(self):
        b = MockBackend()
        await b.inject_source_loss(target="Vgrid")
        kwargs = b.calls[-1][1]
        assert kwargs["signal"] == "Vgrid"
        assert kwargs["value"] == 0.0


# ---------------------------------------------------------------------------
# ECU-side sensor fault (XCP write to a FAULT_* parameter)
# ---------------------------------------------------------------------------

class TestSensorFault:
    @pytest.mark.asyncio
    async def test_sensor_fault_routes_to_write_calibration(self):
        b = MockBackend()
        await b.inject_sensor_fault(signal="V_cell_3", mode="stuck",
                                      value=4.2)
        # The stuck-at fault writes the FAULT_<signal>_<mode> param.
        cal = [c for c in b.calls if c[0] == "write_calibration"]
        assert cal
        assert cal[-1][1]["param"] == "FAULT_V_cell_3_stuck"
        assert cal[-1][1]["value"] == 4.2

    @pytest.mark.asyncio
    async def test_sensor_offset_mode(self):
        b = MockBackend()
        await b.inject_sensor_fault(signal="VDC", mode="offset", value=10.0)
        cal = [c for c in b.calls if c[0] == "write_calibration"]
        assert cal[-1][1]["param"] == "FAULT_VDC_offset"


# ---------------------------------------------------------------------------
# Trip detection
# ---------------------------------------------------------------------------

class TestTripDetection:
    @pytest.mark.asyncio
    async def test_is_tripped_false_when_flag_zero(self):
        b = MockBackend()
        # MockBackend.read_signal returns 0.0 for unset signals.
        assert (await b.is_tripped()) is False

    @pytest.mark.asyncio
    async def test_is_tripped_true_when_flag_high(self):
        b = MockBackend()
        # Seed the mock signal store directly.
        b._signal_values["fault_flag"] = 1.0
        assert (await b.is_tripped()) is True

    @pytest.mark.asyncio
    async def test_expect_trip_returns_true_on_immediate_trip(self):
        b = MockBackend()
        b._signal_values["fault_flag"] = 1.0
        # Short timeout -- already high so first poll succeeds.
        assert await b.expect_trip(within_ms=10.0, poll_ms=1.0) is True

    @pytest.mark.asyncio
    async def test_expect_trip_times_out(self):
        b = MockBackend()
        # Flag stays low -> timeout after a short window.
        result = await b.expect_trip(within_ms=15.0, poll_ms=2.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_clear_fault_pulses_command(self):
        b = MockBackend()
        b._signal_values["fault_flag"] = 1.0
        await b.clear_fault()
        # ``fault_reset`` written twice (rising then falling edge).
        resets = [c for c in b.calls
                   if c[0] == "write_signal"
                   and c[1].get("signal") == "fault_reset"]
        values = [c[1].get("value") for c in resets]
        assert values == [1.0, 0.0]


# ---------------------------------------------------------------------------
# All four backends inherit the same surface
# ---------------------------------------------------------------------------

class TestSurfaceUniformity:
    """Every backend exposes the same fault-injection methods so tests
    written against ``DUTInterface`` work on all of them."""

    @pytest.mark.parametrize("backend_name", ["mock", "hil", "xcp", "hybrid"])
    def test_method_present(self, backend_name):
        from src.tools.dut import get_backend
        b = get_backend(backend_name)
        for method in (
            "inject_overvoltage", "inject_undervoltage",
            "inject_overcurrent", "inject_source_loss",
            "inject_sensor_fault",
            "expect_trip", "is_tripped", "clear_fault",
        ):
            assert callable(getattr(b, method, None)), \
                f"{backend_name} missing {method}"
