"""
Unit tests for the scenario evaluator rule dispatcher (src/evaluator.py).

Exercises each rule family: lockout, timing, thresholds, THD, ROCOF,
current limits, and the strict-mode guard for unknown keys.
"""

from __future__ import annotations

import pytest

from src.evaluator import evaluate, registered_rules
from src.state import WaveformStats


def _s(signal: str, **kw) -> WaveformStats:
    return WaveformStats(signal=signal, **kw)


class TestUnknownRules:
    def test_strict_unknown_key_errors(self):
        stats = [_s("Va", mean=230.0)]
        status, reason = evaluate({"totally_fake_rule": 3.14}, stats, strict=True)
        assert status == "error"
        assert "unknown" in reason.lower()

    def test_nonstrict_ignores_unknown(self):
        stats = [_s("Va", mean=230.0)]
        status, reason = evaluate({"totally_fake_rule": 3.14}, stats, strict=False)
        assert status == "pass"

    def test_empty_rules_always_pass(self):
        assert evaluate({}, []) == ("pass", "")


class TestLockoutAndTrip:
    def test_relay_must_trip_pass(self):
        stats = [_s("BMS_OVP_relay", max=1.0, rise_time_ms=50)]
        s, _ = evaluate({"relay_must_trip": True}, stats)
        assert s == "pass"

    def test_relay_must_trip_no_relay_signal_errors(self):
        stats = [_s("Va", max=1.0)]
        s, r = evaluate({"relay_must_trip": True}, stats)
        assert s == "error"
        assert "relay" in r

    def test_relay_must_trip_did_not_trip(self):
        stats = [_s("BMS_OVP_relay", max=0.0)]
        s, r = evaluate({"relay_must_trip": True}, stats)
        assert s == "fail"

    def test_lockout_must_trip(self):
        stats = [_s("LOCK_OUT_AC", max=1.0)]
        s, _ = evaluate({"lockout_must_trip": True}, stats)
        assert s == "pass"

    def test_both_lockouts_partial_fail(self):
        stats = [_s("LOCK_OUT_AC", max=1.0), _s("LOCK_OUT_DC", max=0.0)]
        s, r = evaluate({"both_lockouts_must_trip": True}, stats)
        assert s == "fail"
        assert "DC" in r


class TestTiming:
    def test_response_time_within_limit(self):
        stats = [_s("relay", rise_time_ms=40, max=1.0)]
        s, _ = evaluate({"response_time_max_ms": 100}, stats)
        assert s == "pass"

    def test_response_time_exceeds(self):
        stats = [_s("relay", rise_time_ms=150, max=1.0)]
        s, r = evaluate({"response_time_max_ms": 100}, stats)
        assert s == "fail"
        assert "150" in r

    def test_response_time_missing_rise_time_errors(self):
        stats = [_s("relay", max=1.0)]  # rise_time_ms=None
        s, r = evaluate({"response_time_max_ms": 100}, stats)
        assert s == "error"

    def test_clearing_time_converts_to_ms(self):
        stats = [_s("LOCK_OUT_AC", rise_time_ms=120, max=1.0)]
        s, _ = evaluate({"clearing_time_max_s": 0.16}, stats)
        assert s == "pass"  # 120ms < 160ms

    def test_clearing_time_fails_when_too_slow(self):
        stats = [_s("LOCK_OUT_AC", rise_time_ms=200, max=1.0)]
        s, _ = evaluate({"clearing_time_max_s": 0.16}, stats)
        assert s == "fail"

    def test_settling_time_max_s(self):
        stats = [_s("w", settling_time_ms=3000, mean=314)]
        s, _ = evaluate({"settling_time_max_s": 5.0}, stats)
        assert s == "pass"

    def test_settling_time_missing_errors(self):
        stats = [_s("w", mean=314)]  # settling_time_ms=None
        s, r = evaluate({"settling_time_max_s": 5.0}, stats)
        assert s == "error"


class TestCurrentPower:
    def test_max_overcurrent_within_limit(self):
        scenario = {"parameters": {"Pref_w": 5000.0, "nominal_voltage_rms_ll": 230.0}}
        # base = 5000 / (1.732*230) ~= 12.55 A
        # peak 1.2 pu = 12.55 * sqrt(2) * 1.2 ~= 21.3 A
        stats = [_s("Ia", max=20.0)]
        s, _ = evaluate({"max_overcurrent_pu": 1.5}, stats, scenario=scenario)
        assert s == "pass"

    def test_max_overcurrent_exceeds(self):
        scenario = {"parameters": {"Pref_w": 5000.0, "nominal_voltage_rms_ll": 230.0}}
        stats = [_s("Ia", max=50.0)]  # well above 1.5 pu
        s, r = evaluate({"max_overcurrent_pu": 1.5}, stats, scenario=scenario)
        assert s == "fail"

    def test_power_tolerance(self):
        stats = [_s("Pe", mean=4900.0)]
        s, _ = evaluate(
            {"power_tolerance_percent": 10.0, "target_p_w": 5000},
            stats,
        )
        assert s == "pass"

    def test_power_tolerance_out_of_band(self):
        stats = [_s("Pe", mean=3000.0)]
        s, r = evaluate(
            {"power_tolerance_percent": 10.0, "target_p_w": 5000},
            stats,
        )
        assert s == "fail"


class TestTHDandROCOF:
    def test_voltage_thd_pass_when_annotated(self):
        stats = [_s("Va", mean=230.0, thd_percent=3.0)]
        s, _ = evaluate({"voltage_thd_max_pct": 5.0}, stats)
        assert s == "pass"

    def test_voltage_thd_fail(self):
        stats = [_s("Va", mean=230.0, thd_percent=8.0)]
        s, _ = evaluate({"voltage_thd_max_pct": 5.0}, stats)
        assert s == "fail"

    def test_voltage_thd_error_when_not_computed(self):
        stats = [_s("Va", mean=230.0)]  # thd_percent=None
        s, r = evaluate({"voltage_thd_max_pct": 5.0}, stats)
        assert s == "error"
        assert "thd" in r.lower()

    def test_rocof_pass(self):
        stats = [_s("w", mean=314.159, rocof_hz_per_s=0.5)]
        s, _ = evaluate({"rocof_max_hz_per_s": 1.0}, stats)
        assert s == "pass"

    def test_rocof_fail(self):
        stats = [_s("w", mean=314.159, rocof_hz_per_s=10.0)]
        s, _ = evaluate({"rocof_max_hz_per_s": 5.0}, stats)
        assert s == "fail"


class TestAllZeroData:
    def test_zero_stats_with_relay_rule_errors(self):
        """Mock-mode all-zero stats should NOT silently pass relay_must_trip."""
        stats = [_s("BMS_OVP_relay", max=0.0, mean=0.0, min=0.0)]
        s, _ = evaluate({"relay_must_trip": True}, stats)
        assert s == "fail"


class TestRegistryCoverage:
    def test_registered_rules_covers_yaml_keys(self):
        registered = set(registered_rules())
        # Sample of keys actually used across scenario YAMLs
        expected = {
            "relay_must_trip", "relay_must_not_trip",
            "response_time_max_ms", "clearing_time_max_s",
            "lockout_must_trip", "both_lockouts_must_trip",
            "must_stay_connected", "must_not_trip",
            "voltage_thd_max_pct", "current_thd_max_pct",
            "max_overcurrent_pu", "ffci_response_max_ms",
            "rocof_max_hz_per_s", "settling_time_max_s",
            "voltage_threshold_pu", "frequency_threshold_hz",
            "dc_link_voltage_max_pu", "min_voltage_pu",
            "power_tolerance_percent", "overshoot_max_percent",
            "direction", "min_change_percent",
        }
        missing = expected - registered
        assert not missing, f"Rules missing from dispatcher: {missing}"
