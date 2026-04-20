"""Tests for src/signal_validator.py"""

from __future__ import annotations

import pytest

from src.signal_validator import (
    attach_validation,
    required_signals,
    validate_all,
    validate_scenario,
)


KNOWN = ["Vgrid", "Va", "Vb", "Vc", "Ia", "Pe", "VLINK_1", "LOCK_OUT_AC",
         "P_ref", "Q_ref", "J", "D", "Kv"]


def _s(**kw):
    return {"scenario_id": kw.pop("scenario_id", "sc1"), **kw}


class TestRequiredSignals:
    def test_measurements(self):
        s = _s(measurements=["Va", "Ia"])
        assert required_signals(s) == {"Va", "Ia"}

    def test_parameters_signal_scalar(self):
        s = _s(parameters={"signal": "Vgrid"})
        assert required_signals(s) == {"Vgrid"}

    def test_parameters_ac_sources(self):
        s = _s(parameters={"signal_ac_sources": ["Vsa", "Vsb", "Vsc"]})
        assert required_signals(s) == {"Vsa", "Vsb", "Vsc"}

    def test_scada_input_and_inputs(self):
        s = _s(parameters={"scada_input": "P_ref",
                            "scada_inputs": ["PFC1_TEMP1", "PFC1_TEMP2"]})
        assert required_signals(s) == {"P_ref", "PFC1_TEMP1", "PFC1_TEMP2"}

    def test_contactor_sequence(self):
        s = _s(parameters={"contactor_sequence": [
            {"signal": "AC_RLY_L1", "action": "close"},
            {"signal": "AC_RLY_L2", "action": "close"},
        ]})
        assert required_signals(s) == {"AC_RLY_L1", "AC_RLY_L2"}

    def test_skip_placeholder_tokens(self):
        s = _s(measurements=["V_cell_{target_cell}", "$var", "Ia"])
        assert required_signals(s) == {"Ia"}

    def test_aggregates_all_sources(self):
        s = _s(measurements=["Va"],
               parameters={"signal_ac_sources": ["Vgrid_a"],
                           "scada_input": "P_ref"})
        assert required_signals(s) == {"Va", "Vgrid_a", "P_ref"}


class TestValidateScenario:
    def test_clean_scenario_has_no_errors(self):
        s = _s(measurements=["Va", "Ia"])
        assert validate_scenario(s, KNOWN) == []

    def test_missing_signal_flagged(self):
        s = _s(measurements=["DoesNotExist"])
        errs = validate_scenario(s, KNOWN)
        assert len(errs) == 1
        assert "DoesNotExist" in errs[0]

    def test_mixed_known_and_unknown(self):
        s = _s(measurements=["Va", "Bogus1"],
               parameters={"signal_ac_sources": ["Vgrid", "Bogus2"]})
        errs = validate_scenario(s, KNOWN)
        assert len(errs) == 2
        assert any("Bogus1" in e for e in errs)
        assert any("Bogus2" in e for e in errs)

    def test_empty_model_signals_skips_check(self):
        """When signal discovery failed, don't spuriously flag everything."""
        s = _s(measurements=["Anything"])
        assert validate_scenario(s, []) == []


class TestValidateAll:
    def test_returns_only_failing_scenarios(self):
        scens = [
            _s(scenario_id="ok", measurements=["Va"]),
            _s(scenario_id="bad1", measurements=["Bogus"]),
            _s(scenario_id="bad2", parameters={"signal": "Nope"}),
        ]
        result = validate_all(scens, KNOWN)
        assert set(result.keys()) == {"bad1", "bad2"}

    def test_all_clean(self):
        scens = [_s(scenario_id="a", measurements=["Va"]),
                 _s(scenario_id="b", measurements=["Ia"])]
        assert validate_all(scens, KNOWN) == {}


class TestAttachValidation:
    def test_annotates_in_place(self):
        scens = [
            {"scenario_id": "good", "measurements": ["Va"]},
            {"scenario_id": "bad", "measurements": ["BogusSig"]},
        ]
        n_bad = attach_validation(scens, KNOWN)
        assert n_bad == 1
        assert "validation_errors" not in scens[0]
        assert scens[1]["validation_errors"] == ["signal not in model: BogusSig"]

    def test_removes_stale_errors_when_fixed(self):
        scens = [{"scenario_id": "x", "measurements": ["Va"],
                  "validation_errors": ["from previous run"]}]
        attach_validation(scens, KNOWN)
        assert "validation_errors" not in scens[0]
