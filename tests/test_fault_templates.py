"""
Unit tests for the fault_templates registry and its integration with
execute_scenario._apply_stimulus.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.fault_templates import (
    FAULT_TEMPLATES,
    get_template,
    validate_params,
)
from src.nodes.execute_scenario import _apply_stimulus


def _mock_hil() -> AsyncMock:
    hil = AsyncMock()
    hil.execute = AsyncMock(return_value={"ok": True})
    return hil


class TestRegistry:
    def test_five_templates_registered(self):
        expected = {
            "overvoltage",
            "undervoltage",
            "short_circuit",
            "open_circuit",
            "frequency_deviation",
        }
        assert expected == set(FAULT_TEMPLATES.keys())

    def test_get_template_unknown(self):
        assert get_template("nonexistent") is None

    def test_validate_params_missing(self):
        t = FAULT_TEMPLATES["overvoltage"]
        assert "signal" in validate_params(t, {"fault_value": 5.0})
        assert validate_params(t, {"signal": "V", "fault_value": 5.0}) == []


class TestOvervoltageTemplate:
    async def test_ramp_issued(self):
        hil = _mock_hil()
        await FAULT_TEMPLATES["overvoltage"].apply(hil, {
            "signal": "V_cell_1",
            "nominal_value": 3.6,
            "fault_value": 4.5,
            "ramp_duration_s": 0.05,
            "hold_after_s": 0.0,
        })
        calls = [c.args[0] for c in hil.execute.await_args_list]
        assert calls == ["hil_signal_write", "hil_signal_write"]
        ramp_params = hil.execute.await_args_list[1].args[1]
        assert ramp_params["waveform"] == "ramp"
        assert ramp_params["end_value"] == 4.5


class TestUndervoltageTemplate:
    async def test_ramp_direction(self):
        hil = _mock_hil()
        await FAULT_TEMPLATES["undervoltage"].apply(hil, {
            "signal": "V_cell_1",
            "nominal_value": 3.6,
            "fault_value": 2.4,
            "ramp_duration_s": 0.05,
            "hold_after_s": 0.0,
        })
        ramp_params = hil.execute.await_args_list[1].args[1]
        assert ramp_params["start_value"] == 3.6
        assert ramp_params["end_value"] == 2.4


class TestShortCircuitTemplate:
    async def test_dispatches_to_fault_inject(self):
        hil = _mock_hil()
        await FAULT_TEMPLATES["short_circuit"].apply(hil, {
            "switch_name": "S8_top",
            "hold_after_s": 0.0,
        })
        tool, args = hil.execute.await_args_list[0].args
        assert tool == "hil_fault_inject"
        assert args["fault_type"] == "switch_short"
        assert args["target"] == "S8_top"


class TestOpenCircuitTemplate:
    async def test_dispatches_switch_open(self):
        hil = _mock_hil()
        await FAULT_TEMPLATES["open_circuit"].apply(hil, {
            "switch_name": "S8_top",
            "hold_after_s": 0.0,
        })
        args = hil.execute.await_args_list[0].args[1]
        assert args["fault_type"] == "switch_open"


class TestFrequencyDeviationTemplate:
    async def test_sine_with_deviated_frequency(self):
        hil = _mock_hil()
        await FAULT_TEMPLATES["frequency_deviation"].apply(hil, {
            "signal": "V_grid_L1",
            "amplitude": 311.0,
            "deviated_frequency_hz": 62.5,
            "hold_after_s": 0.0,
        })
        args = hil.execute.await_args_list[0].args[1]
        assert args["waveform"] == "sine"
        assert args["frequency_hz"] == 62.5


class TestStimulusDispatch:
    async def test_template_path_invoked(self):
        hil = _mock_hil()
        await _apply_stimulus(hil, {
            "fault_template": "overvoltage",
            "signal": "V_cell_1",
            "nominal_value": 3.6,
            "fault_value": 4.5,
            "ramp_duration_s": 0.01,
            "hold_after_s": 0.0,
        })
        # Two signal_write calls = template executed, not fallback chain.
        tools_called = [c.args[0] for c in hil.execute.await_args_list]
        assert tools_called == ["hil_signal_write", "hil_signal_write"]

    async def test_unknown_template_raises(self):
        hil = _mock_hil()
        with pytest.raises(ValueError, match="Unknown fault_template"):
            await _apply_stimulus(hil, {"fault_template": "made_up"})

    async def test_missing_required_params_raises(self):
        hil = _mock_hil()
        with pytest.raises(ValueError, match="missing params"):
            await _apply_stimulus(hil, {"fault_template": "overvoltage"})

    async def test_legacy_fallback_still_works(self):
        """Scenarios without fault_template use the existing elif chain."""
        hil = _mock_hil()
        await _apply_stimulus(hil, {
            "target_cell": 1,
            "normal_voltage": 3.6,
            "fault_voltage": 4.5,
            "ramp_duration_s": 0.01,
        })
        tools_called = [c.args[0] for c in hil.execute.await_args_list]
        assert "hil_signal_write" in tools_called
