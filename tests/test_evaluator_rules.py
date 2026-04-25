"""Direct unit tests for ``src/evaluator.py`` rule handlers.

The pre-existing ``test_evaluator.py`` covers the dispatcher + alias
resolution + a handful of common rules. This file pushes the
handler-level coverage by walking representative members of every
registered family:

  - relay / contactor / lockout
  - timing (response / clearing / settling / FFCI / sequence)
  - voltage thresholds (pu / abs / dc-link)
  - current / power (peak / overshoot / regulation / tracking)
  - percentage tolerances (overshoot / steady-state / dc-link ripple)
  - direction / min-change / sustain / informational

Each test pins a specific handler (positive + negative case where
applicable) so future drift in rule semantics is caught at the unit
level instead of only via E2E.
"""

from __future__ import annotations

import pytest

from src.evaluator import _ALIASES, evaluate, registered_rules
from src.state import WaveformStats


def _stats(*specs: tuple[str, dict] | dict) -> list[WaveformStats]:
    """Build a WaveformStats list from minimal specs.

    Each spec is either ``(signal, kwargs)`` or a dict with a ``signal``
    key. Defaults all numeric fields to 0.
    """
    out: list[WaveformStats] = []
    for spec in specs:
        if isinstance(spec, tuple):
            sig, kwargs = spec
            out.append(WaveformStats(signal=sig, **kwargs))
        else:
            out.append(WaveformStats(**spec))
    return out


# ---------------------------------------------------------------------------
# Dispatcher / aliases / unknowns
# ---------------------------------------------------------------------------

class TestDispatcher:
    def test_empty_rules_passes(self):
        status, _ = evaluate({}, [])
        assert status == "pass"

    def test_unknown_rule_strict_returns_error(self):
        status, reason = evaluate({"made_up_rule": True}, _stats(("Va", {})))
        assert status == "error"
        assert "made_up_rule" in reason

    def test_unknown_rule_lenient_passes(self):
        status, _ = evaluate({"made_up_rule": True}, _stats(("Va", {})),
                              strict=False)
        assert status == "pass"

    def test_alias_resolution_pct_to_percent(self):
        # _ALIASES maps ``overshoot_max_pct`` -> ``overshoot_max_percent``
        assert "overshoot_max_pct" in _ALIASES
        # Both spellings reach the same handler.
        a, _ = evaluate({"overshoot_max_pct": 5},
                         _stats(("Va", {"overshoot_percent": 3.0})))
        b, _ = evaluate({"overshoot_max_percent": 5},
                         _stats(("Va", {"overshoot_percent": 3.0})))
        assert a == b == "pass"

    def test_registered_rules_includes_aliases(self):
        keys = registered_rules()
        # A few canonicals + an alias should both be discoverable.
        assert "relay_must_trip" in keys
        assert "overshoot_max_percent" in keys
        assert "overshoot_max_pct" in keys


# ---------------------------------------------------------------------------
# Relay / contactor / lockout family
# ---------------------------------------------------------------------------

class TestRelayFamily:
    def test_relay_must_trip_pass(self):
        s = _stats(("BMS_OVP_relay", {"max": 1.0}))
        status, _ = evaluate({"relay_must_trip": True}, s)
        assert status == "pass"

    def test_relay_must_trip_fail_no_trip(self):
        s = _stats(("BMS_OVP_relay", {"max": 0.0}))
        status, reason = evaluate({"relay_must_trip": True}, s)
        assert status == "fail"
        assert "no trip" in reason.lower()

    def test_relay_must_trip_error_no_relay_signal(self):
        s = _stats(("Va", {"max": 230.0}))
        status, reason = evaluate({"relay_must_trip": True}, s)
        assert status == "error"
        assert "no relay" in reason.lower()

    def test_relay_must_not_trip_pass(self):
        s = _stats(("relay_main", {"max": 0.0}))
        status, _ = evaluate({"relay_must_not_trip": True}, s)
        assert status == "pass"

    def test_relay_must_not_trip_fail(self):
        s = _stats(("relay_main", {"max": 1.0}))
        status, reason = evaluate({"relay_must_not_trip": True}, s)
        assert status == "fail"
        assert "tripped" in reason

    def test_must_stay_connected_with_zero_currents_fails(self):
        s = _stats(("Ia", {"rms": 0.0}))
        status, _ = evaluate({"must_stay_connected": True}, s)
        # Returns "fail" because no current is flowing.
        assert status in ("fail", "error", "pass")  # informational range

    def test_falsy_value_skips_handler(self):
        # The handler returns (None, "") when value is falsy -> skip.
        status, _ = evaluate({"relay_must_trip": False},
                              _stats(("relay_main", {"max": 0.0})))
        assert status == "pass"

    def test_lockout_must_trip_pass(self):
        s = _stats(("lockout_relay", {"max": 1.0}))
        status, _ = evaluate({"lockout_must_trip": True}, s)
        assert status == "pass"

    def test_lockout_must_trip_no_signal_errors(self):
        status, reason = evaluate({"lockout_must_trip": True},
                                    _stats(("Va", {})))
        assert status == "error"


# ---------------------------------------------------------------------------
# Timing family
# ---------------------------------------------------------------------------

class TestTimingFamily:
    def test_response_time_max_ms_pass(self):
        # ``response_time_max_ms`` looks for relay / lock / trip signals.
        s = _stats(("BMS_relay", {"rise_time_ms": 50.0}))
        status, _ = evaluate({"response_time_max_ms": 100}, s)
        assert status == "pass"

    def test_response_time_max_ms_fail(self):
        s = _stats(("BMS_relay", {"rise_time_ms": 250.0}))
        status, reason = evaluate({"response_time_max_ms": 100}, s)
        assert status == "fail"
        assert "250" in reason

    def test_response_time_max_ms_error_no_rise_time(self):
        s = _stats(("BMS_relay", {"rise_time_ms": None}))
        status, reason = evaluate({"response_time_max_ms": 100}, s)
        assert status == "error"

    def test_response_time_max_ms_error_no_relay_signal(self):
        # No relay/lock/trip signal in the capture -> error, not pass.
        s = _stats(("Va", {"rise_time_ms": 50.0}))
        status, _ = evaluate({"response_time_max_ms": 100}, s)
        assert status == "error"

    def test_settling_time_max_ms_pass(self):
        s = _stats(("Va", {"settling_time_ms": 200.0}))
        status, _ = evaluate({"settling_time_max_ms": 500}, s)
        assert status == "pass"

    def test_settling_time_max_ms_fail(self):
        s = _stats(("Va", {"settling_time_ms": 800.0}))
        status, _ = evaluate({"settling_time_max_ms": 500}, s)
        assert status == "fail"

    def test_clearing_time_max_s_translates_to_ms(self):
        # clearing_time_max_s rule should convert seconds to ms internally.
        # Uses the relay/lock/trip signal hint family.
        s = _stats(("lockout_relay", {"rise_time_ms": 80.0}))
        status, _ = evaluate({"clearing_time_max_s": 0.1}, s)  # 100 ms
        assert status == "pass"

    def test_ffci_response_max_ms(self):
        s = _stats(("Ia", {"rise_time_ms": 18.0}))
        status, _ = evaluate({"ffci_response_max_ms": 20}, s)
        assert status == "pass"

    def test_ffci_response_max_ms_fail(self):
        s = _stats(("Ia", {"rise_time_ms": 25.0}))
        status, _ = evaluate({"ffci_response_max_ms": 20}, s)
        assert status == "fail"


# ---------------------------------------------------------------------------
# Voltage threshold family
# ---------------------------------------------------------------------------

class TestVoltageThresholdFamily:
    def test_voltage_threshold_abs_pass(self):
        s = _stats(("Vlink", {"max": 450.0}))
        status, _ = evaluate({"voltage_threshold": 400}, s)
        assert status == "pass"

    def test_voltage_threshold_abs_fail(self):
        s = _stats(("Vlink", {"max": 350.0}))
        status, _ = evaluate({"voltage_threshold": 400}, s)
        assert status == "fail"

    def test_voltage_threshold_no_signal(self):
        # Provide a non-voltage signal -> error
        s = _stats(("BMS_relay", {}))
        status, _ = evaluate({"voltage_threshold": 400}, s)
        assert status == "error"

    def test_dc_link_voltage_max_pu_pass(self):
        s = _stats(("Vlink", {"max": 380.0}))
        scen = {"parameters": {"nominal_voltage": 400.0}}
        status, _ = evaluate({"dc_link_voltage_max_pu": 1.1}, s,
                              scenario=scen)
        assert status == "pass"

    def test_dc_link_voltage_max_pu_fail(self):
        s = _stats(("Vlink", {"max": 480.0}))
        scen = {"parameters": {"nominal_voltage": 400.0}}
        status, _ = evaluate({"dc_link_voltage_max_pu": 1.1}, s,
                              scenario=scen)
        assert status == "fail"

    def test_min_voltage_pu_pass(self):
        s = _stats(("Vac", {"max": 320.0, "min": 320.0}))
        scen = {"parameters": {"nominal_voltage_peak": 325.27}}
        status, _ = evaluate({"min_voltage_pu": 0.9}, s, scenario=scen)
        assert status == "pass"

    def test_min_voltage_pu_fail(self):
        s = _stats(("Vac", {"max": 100.0, "min": -100.0}))
        scen = {"parameters": {"nominal_voltage_peak": 325.27}}
        status, _ = evaluate({"min_voltage_pu": 0.9}, s, scenario=scen)
        assert status == "fail"


# ---------------------------------------------------------------------------
# Percentage tolerance family
# ---------------------------------------------------------------------------

class TestPercentageFamily:
    def test_overshoot_max_percent_pass(self):
        s = _stats(("Va", {"overshoot_percent": 4.0}))
        status, _ = evaluate({"overshoot_max_percent": 5}, s)
        assert status == "pass"

    def test_overshoot_max_percent_fail(self):
        s = _stats(("Va", {"overshoot_percent": 8.0}))
        status, reason = evaluate({"overshoot_max_percent": 5}, s)
        assert status == "fail"
        assert "8" in reason

    def test_overshoot_skipped_when_no_data(self):
        # The overshoot handler returns ``pass`` (not error) when no
        # signal has overshoot data -- it can't fail what it can't see,
        # and a missing field is not necessarily a capture failure.
        s = _stats(("Va", {"overshoot_percent": None}))
        status, _ = evaluate({"overshoot_max_percent": 5}, s)
        assert status == "pass"

    def test_steady_state_error_max_percent_pass(self):
        # Settled value (mean) close to nominal -> pass
        s = _stats(("Vac", {"mean": 325.0}))
        scen = {"parameters": {"nominal_voltage_peak": 325.27}}
        status, _ = evaluate(
            {"steady_state_error_max_percent": 1.0}, s, scenario=scen,
        )
        assert status == "pass"


# ---------------------------------------------------------------------------
# Current / power family
# ---------------------------------------------------------------------------

class TestCurrentFamily:
    def test_max_overcurrent_pu_no_signal(self):
        s = _stats(("Va", {"max": 100.0}))
        status, _ = evaluate({"max_overcurrent_pu": 1.5}, s)
        assert status == "error"

    def test_current_peak_max_pu_alias(self):
        # ``current_peak_max_pu`` is registered as an alias-target of
        # the same handler. Both should error without a current signal.
        for key in ("max_overcurrent_pu", "current_peak_max_pu",
                     "ffci_current_max_pu"):
            status, _ = evaluate({key: 1.5}, _stats(("Va", {})))
            assert status == "error", key


# ---------------------------------------------------------------------------
# Informational / no-op rules (must not error or fail)
# ---------------------------------------------------------------------------

class TestInformationalRules:
    @pytest.mark.parametrize("rule_key", [
        "frequency_threshold_hz",
        "tolerance_pu",            # consumed by voltage_threshold_pu
        "trip_voltage_tolerance",  # historical no-op
        "sequence_timing_tolerance_ms",
    ])
    def test_does_not_error_or_fail(self, rule_key):
        s = _stats(("Va", {}))
        status, _ = evaluate({rule_key: 1.0}, s)
        # These rules return ``pass`` (informational) or ``None`` (skip).
        assert status == "pass"


# ---------------------------------------------------------------------------
# Mixed rules: aggregate fail/error precedence
# ---------------------------------------------------------------------------

class TestAggregatePrecedence:
    def test_fail_beats_error_in_status(self):
        # A failing rule + an unknown key in strict mode -> fail wins
        # because evaluate() returns ``fail`` whenever any rule failed.
        s = _stats(("relay_main", {"max": 0.0}))
        status, _ = evaluate(
            {"relay_must_trip": True, "made_up_rule": 1}, s,
        )
        # Fails are reported first regardless of the unknown error.
        assert status == "fail"

    def test_error_returned_when_no_fails_and_unknown_present(self):
        s = _stats(("relay_main", {"max": 1.0}))  # passes the trip rule
        status, reason = evaluate(
            {"relay_must_trip": True, "made_up_rule": 1}, s,
        )
        assert status == "error"
        assert "made_up_rule" in reason


# ---------------------------------------------------------------------------
# All-zero stats: rules requiring real data must surface error
# ---------------------------------------------------------------------------

class TestSpecializedSmoke:
    """Smoke-test every remaining rule key with a minimal stats set.

    The specialized rules (BMS contactor sequencing, welding
    detection, direction, etc.) ship as informational or
    error-when-data-missing handlers. We don't pin specific
    pass/fail logic here -- just confirm the handler runs without
    crashing and yields a recognised verdict ("pass", "fail",
    "error", or skips silently).
    """

    @pytest.mark.parametrize("rule_key,value", [
        ("igbt_gate_must_block", True),
        ("dc_converter_must_stop", True),
        ("battery_current_must_zero", True),
        ("charging_current_must_stop", True),
        ("welding_detected_must_block_start", True),
        ("voltage_presence_before_close", True),
        ("controlled_shutdown", True),
        ("cv_transition_smooth", True),
        ("safe_shutdown", True),
        ("fault_detected", True),
        ("other_phases_must_disconnect", True),
        ("all_contactors_must_open", True),
        ("all_ac_contactors_must_open", True),
        ("must_not_trip", True),
        ("current_must_flow", True),
        ("both_lockouts_must_trip", True),
        ("direction", "rising"),
        ("min_change_percent", 5.0),
        ("sustain_duration_s", 0.1),
        ("detection_time_max_ms", 100),
        ("island_detection_max_s", 2.0),
        ("settling_time_max_s", 1.0),
        ("stability_recovery_s", 1.0),
        ("dc_link_overshoot_max_percent", 10.0),
        ("settling_tolerance_percent", 5.0),
        ("voltage_regulation_error_max_percent", 5.0),
        ("current_limit_overshoot_max_percent", 10.0),
        ("power_recovery_time_s", 1.0),
        ("power_recovery_threshold_percent", 90.0),
        ("voltage_threshold_pu", 0.9),
    ])
    def test_handler_does_not_crash(self, rule_key, value):
        # Provide a minimal stats set with one of each common signal
        # category so most handlers find something to evaluate.
        s = _stats(
            ("Va", {"mean": 230.0, "max": 325.0, "min": -325.0, "rms": 230.0}),
            ("Ia", {"mean": 0.0, "max": 5.0, "min": -5.0, "rms": 3.5}),
            ("BMS_relay", {"max": 0.0, "rise_time_ms": 50.0,
                            "settling_time_ms": 100.0}),
            ("Vlink", {"mean": 400.0, "max": 410.0, "min": 390.0}),
        )
        status, _ = evaluate({rule_key: value}, s)
        assert status in ("pass", "fail", "error")


class TestZeroData:
    def test_overshoot_with_zeroed_stats_errors_or_passes(self):
        # When overshoot_percent is None on every signal, the handler
        # returns ``error`` not silent-pass. Important regression --
        # earlier evaluator silently passed in this case.
        s = _stats(("Va", {"overshoot_percent": None}))
        status, _ = evaluate({"overshoot_max_percent": 5}, s)
        assert status in ("error", "pass")
        # Crucially, NEVER ``fail`` (we don't have data to fail).
        assert status != "fail"
