"""Tests for the P0+P1 production-readiness improvements."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import cost_guard, heartbeat, liveness
from src.validator import SafetyConfig, WRITABLE_XCP_PARAMS


# ---------------------------------------------------------------------------
# P0 #1 -- SafetyConfig overlay
# ---------------------------------------------------------------------------

class TestSafetyOverlay:
    def test_default_values(self):
        s = SafetyConfig()
        assert s.max_voltage == 60.0
        assert s.max_current == 200.0

    def test_from_overlay_lifts_voltage(self):
        s = SafetyConfig.from_overlay({"max_voltage": 900, "max_current": 250})
        assert s.max_voltage == 900
        assert s.max_current == 250
        # other defaults preserved
        assert s.auto_retry_limit == 3

    def test_from_overlay_replaces_whitelist(self):
        s = SafetyConfig.from_overlay({
            "writable_xcp_params": ["only_one_param"],
        })
        assert s.writable_xcp_params == {"only_one_param"}

    def test_from_overlay_unknown_keys_ignored(self):
        s = SafetyConfig.from_overlay({"max_voltage": 100, "future_field": 42})
        assert s.max_voltage == 100  # known: applied
        # forward-compat: unknown ignored, no exception

    def test_default_whitelist_includes_canonicals(self):
        s = SafetyConfig.from_overlay({"max_voltage": 100})
        # Falls back to the module-level WRITABLE_XCP_PARAMS.
        assert s.writable_xcp_params == set(WRITABLE_XCP_PARAMS)


# ---------------------------------------------------------------------------
# P0 #4 -- cost guard + diagnosis cache
# ---------------------------------------------------------------------------

class TestCostGuard:
    def test_consume_under_cap(self, monkeypatch):
        monkeypatch.setenv("THAA_MAX_CLAUDE_CALLS_PER_RUN", "5")
        cost_guard.reset_call_count()
        for _ in range(5):
            assert cost_guard.consume_one_call() is True

    def test_consume_blocks_at_cap(self, monkeypatch):
        monkeypatch.setenv("THAA_MAX_CLAUDE_CALLS_PER_RUN", "2")
        cost_guard.reset_call_count()
        assert cost_guard.consume_one_call() is True
        assert cost_guard.consume_one_call() is True
        assert cost_guard.consume_one_call() is False

    def test_calls_remaining(self, monkeypatch):
        monkeypatch.setenv("THAA_MAX_CLAUDE_CALLS_PER_RUN", "3")
        cost_guard.reset_call_count()
        assert cost_guard.claude_calls_remaining() == 3
        cost_guard.consume_one_call()
        assert cost_guard.claude_calls_remaining() == 2

    def test_synthetic_escalate_diagnosis_shape(self):
        d = cost_guard.synthetic_escalate_diagnosis("vsm_x", "cap reached")
        assert d["corrective_action_type"] == "escalate"
        assert d["confidence"] == 0.0
        assert d["root_cause_category"] == "cost_guard"


class TestDiagnosisCache:
    def test_record_and_lookup_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("THAA_DIAGNOSIS_CACHE_PATH",
                            str(tmp_path / "cache.jsonl"))
        monkeypatch.delenv("THAA_DIAGNOSIS_CACHE", raising=False)

        failed = {
            "scenario_id": "vsm_x", "status": "fail",
            "fail_reason": "no trip",
            "waveform_stats": [{"signal": "Va", "max": 0.0}],
        }
        diag = {"corrective_action_type": "xcp_calibration",
                 "corrective_param": "J", "corrective_value": 0.35}

        # First lookup misses
        assert cost_guard.lookup_cached_diagnosis("vsm_x", failed) is None
        cost_guard.record_cached_diagnosis("vsm_x", failed, diag)
        # Second lookup hits
        hit = cost_guard.lookup_cached_diagnosis("vsm_x", failed)
        assert hit == diag

    def test_disabled_via_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("THAA_DIAGNOSIS_CACHE_PATH",
                            str(tmp_path / "cache.jsonl"))
        monkeypatch.setenv("THAA_DIAGNOSIS_CACHE", "off")
        cost_guard.record_cached_diagnosis(
            "vsm_x", {"scenario_id": "vsm_x"}, {"x": 1},
        )
        # Disabled write -> no file
        assert not (tmp_path / "cache.jsonl").exists()
        assert cost_guard.lookup_cached_diagnosis(
            "vsm_x", {"scenario_id": "vsm_x"},
        ) is None

    def test_signature_stable_across_float_noise(self, tmp_path, monkeypatch):
        # Two failures with sub-rounding-noise differences in float
        # stats hit the same cache entry.
        monkeypatch.setenv("THAA_DIAGNOSIS_CACHE_PATH",
                            str(tmp_path / "cache.jsonl"))
        a = {"scenario_id": "x", "status": "fail",
              "waveform_stats": [{"signal": "Va", "max": 1.000001}]}
        b = {"scenario_id": "x", "status": "fail",
              "waveform_stats": [{"signal": "Va", "max": 1.000002}]}
        cost_guard.record_cached_diagnosis("x", a, {"d": 1})
        # ``b`` rounds to the same 4-decimal canonical form -> cache hit.
        assert cost_guard.lookup_cached_diagnosis("x", b) == {"d": 1}


# ---------------------------------------------------------------------------
# P1 #6 -- heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_disabled_when_env_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("THAA_HEARTBEAT_PATH", raising=False)
        heartbeat.beat(node="execute_scenario", state={})
        # No write happened -- inspect tmp_path stays empty.
        assert list(tmp_path.iterdir()) == []

    def test_writes_json_payload(self, tmp_path, monkeypatch):
        path = tmp_path / "hb.json"
        monkeypatch.setenv("THAA_HEARTBEAT_PATH", str(path))
        heartbeat.beat(node="execute_scenario", state={
            "current_scenario": {"scenario_id": "vsm_x", "domain": "grid",
                                   "device_id": "hil_a"},
            "scenario_index": 5,
            "scenarios": [{}] * 10,
            "results": [{"status": "pass"}, {"status": "fail"},
                         {"status": "pass"}],
        })
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["node"] == "execute_scenario"
        assert rec["scenario_id"] == "vsm_x"
        assert rec["domain"] == "grid"
        assert rec["device_id"] == "hil_a"
        assert rec["passed"] == 2
        assert rec["failed"] == 1
        assert rec["remaining"] == 5

    def test_overwrite_on_each_tick(self, tmp_path, monkeypatch):
        path = tmp_path / "hb.json"
        monkeypatch.setenv("THAA_HEARTBEAT_PATH", str(path))
        heartbeat.beat(node="a", state={})
        heartbeat.beat(node="b", state={})
        # Single line file -- the second write replaces the first.
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["node"] == "b"


# ---------------------------------------------------------------------------
# P1 #10 -- liveness probe
# ---------------------------------------------------------------------------

class TestLivenessProbe:
    def test_disabled_by_default(self):
        # Without THAA_LIVENESS_PROBE, observe never alerts.
        for _ in range(10):
            alert, _ = liveness.observe("hil", [{"mean": 0, "max": 0,
                                                    "min": 0}])
            assert alert is False

    def test_mock_backend_never_alerts(self, monkeypatch):
        monkeypatch.setenv("THAA_LIVENESS_PROBE", "on")
        liveness.reset()
        for _ in range(10):
            alert, _ = liveness.observe("mock", [{"mean": 0, "max": 0,
                                                     "min": 0}])
            assert alert is False

    def test_3_consecutive_flatlines_alerts(self, monkeypatch):
        monkeypatch.setenv("THAA_LIVENESS_PROBE", "on")
        liveness.reset()
        flat = [{"signal": "Va", "mean": 0, "max": 0, "min": 0}]
        a1, _ = liveness.observe("hil", flat)
        a2, _ = liveness.observe("hil", flat)
        a3, msg = liveness.observe("hil", flat)
        assert (a1, a2) == (False, False)
        assert a3 is True
        assert "disconnected" in msg

    def test_real_data_resets_counter(self, monkeypatch):
        monkeypatch.setenv("THAA_LIVENESS_PROBE", "on")
        liveness.reset()
        flat = [{"mean": 0, "max": 0, "min": 0}]
        good = [{"mean": 230.0, "max": 325.0, "min": -325.0}]
        liveness.observe("hil", flat)
        liveness.observe("hil", flat)
        # Real signal -> counter resets
        liveness.observe("hil", good)
        # Two more flatlines is not enough to alert (need 3 consecutive)
        a, _ = liveness.observe("hil", flat)
        b, _ = liveness.observe("hil", flat)
        assert a is False and b is False


# ---------------------------------------------------------------------------
# P1 #11 -- audit rotation
# ---------------------------------------------------------------------------

class TestAuditRotation:
    def test_rotation_off_uses_base_path(self, tmp_path, monkeypatch):
        from src.audit import _audit_path, _rotated_path
        monkeypatch.setenv("THAA_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
        monkeypatch.setenv("THAA_AUDIT_ROTATE", "off")
        assert _rotated_path(_audit_path()) == _audit_path()

    def test_rotation_default_appends_yyyymm(self, tmp_path, monkeypatch):
        from src.audit import _audit_path, _rotated_path
        monkeypatch.setenv("THAA_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
        monkeypatch.delenv("THAA_AUDIT_ROTATE", raising=False)
        rotated = _rotated_path(_audit_path())
        # Looks like ``audit-YYYY-MM.jsonl``
        assert rotated.suffix == ".jsonl"
        assert "audit-" in rotated.name
        assert len(rotated.stem) == len("audit-2026-04")
