"""Sprint-2 quality tests: deepcopy isolation, prompt externalization,
audit trail, heal-edge wiring helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.audit import record_hitl_decision
from src.domain_classifier import _reset_overlay_cache, overlay_for
from src.graph import build_graph, wire_heal_edges
from src.graph_orchestrator import build_orchestrator_graph, fan_out_parallel
from langgraph.graph import StateGraph


# ---------------------------------------------------------------------------
# Phase 4-F deepcopy: branch state must not share nested dicts with parent
# ---------------------------------------------------------------------------

class TestParallelDeepcopy:
    def test_dut_config_is_isolated(self):
        parent_config = {"a2l_path": "fw.a2l", "nested": {"k": 1}}
        state = {
            "scenarios": [{"scenario_id": "g1", "domain": "grid"}],
            "domain_counts": {"grid": 1},
            "dut_config": parent_config,
            "device_pool": {"hil_a": {"a2l_path": "fw_a.a2l"}},
        }
        sends = fan_out_parallel(state)
        assert len(sends) == 1
        branch = sends[0].arg
        # Branch's dut_config is a *copy* — mutating it must not affect parent.
        branch["dut_config"]["nested"]["k"] = 999
        assert parent_config["nested"]["k"] == 1, (
            "branch state mutation leaked back into parent dut_config"
        )

    def test_device_pool_is_isolated(self):
        pool = {"hil_a": {"a2l_path": "fw_a.a2l"}}
        state = {
            "scenarios": [{"scenario_id": "g1", "domain": "grid"}],
            "domain_counts": {"grid": 1},
            "dut_config": {},
            "device_pool": pool,
        }
        sends = fan_out_parallel(state)
        sends[0].arg["device_pool"]["hil_a"]["a2l_path"] = "tampered.a2l"
        assert pool["hil_a"]["a2l_path"] == "fw_a.a2l"

    def test_scenarios_are_isolated(self):
        scenarios = [
            {"scenario_id": "b1", "domain": "bms", "parameters": {"x": 1}},
        ]
        state = {
            "scenarios": scenarios,
            "domain_counts": {"bms": 1},
        }
        sends = fan_out_parallel(state)
        sends[0].arg["scenarios"][0]["parameters"]["x"] = 999
        assert scenarios[0]["parameters"]["x"] == 1


# ---------------------------------------------------------------------------
# Domain prompts on disk
# ---------------------------------------------------------------------------

class TestPromptExternalization:
    def setup_method(self):
        _reset_overlay_cache()

    def test_bms_overlay_loaded_from_disk(self):
        text = overlay_for("bms")
        assert text != ""
        assert "BMS" in text or "battery" in text.lower()

    def test_grid_overlay_loaded_from_disk(self):
        text = overlay_for("grid")
        assert "grid" in text.lower() or "GFM" in text

    def test_pcs_overlay_loaded_from_disk(self):
        text = overlay_for("pcs")
        assert "PCS" in text or "Ctrl_K" in text

    def test_general_has_no_overlay_file(self):
        # By design: no prompts/domains/general.md
        assert overlay_for("general") == ""

    def test_unknown_domain_returns_empty(self):
        assert overlay_for("nonsense") == ""

    def test_overlay_is_cached(self):
        a = overlay_for("bms")
        b = overlay_for("bms")
        assert a is b  # same string identity == same cache hit


# ---------------------------------------------------------------------------
# HITL audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_record_writes_jsonl_line(self, tmp_path, monkeypatch):
        log = tmp_path / "audit.jsonl"
        monkeypatch.setenv("THAA_AUDIT_PATH", str(log))
        monkeypatch.setenv("THAA_OPERATOR", "tester@example.com")
        record_hitl_decision(
            thread_id="thaa-cli-9",
            decision="approve",
            scenario={"scenario_id": "vsm_x", "domain": "grid",
                      "device_id": "hil_a"},
            diagnosis={"corrective_action_type": "xcp_calibration",
                       "corrective_param": "J", "corrective_value": 0.35,
                       "confidence": 0.9,
                       "root_cause_description": "low J"},
        )
        line = log.read_text(encoding="utf-8").strip().splitlines()[-1]
        rec = json.loads(line)
        assert rec["decision"] == "approve"
        assert rec["scenario_id"] == "vsm_x"
        assert rec["param"] == "J"
        assert rec["value"] == 0.35
        assert rec["operator"] == "tester@example.com"
        assert rec["device_id"] == "hil_a"

    def test_disabled_via_env(self, tmp_path, monkeypatch):
        log = tmp_path / "audit.jsonl"
        monkeypatch.setenv("THAA_AUDIT_PATH", str(log))
        monkeypatch.setenv("THAA_AUDIT", "off")
        record_hitl_decision(
            thread_id="t", decision="reject",
            scenario={"scenario_id": "x"}, diagnosis={},
        )
        # File must not exist (or be empty) -- audit was disabled.
        assert not log.exists() or log.read_text(encoding="utf-8") == ""

    def test_three_decisions_append(self, tmp_path, monkeypatch):
        log = tmp_path / "audit.jsonl"
        monkeypatch.setenv("THAA_AUDIT_PATH", str(log))
        for decision in ("approve", "reject", "abort"):
            record_hitl_decision(
                thread_id="t", decision=decision,
                scenario={"scenario_id": "s"}, diagnosis={},
            )
        lines = log.read_text(encoding="utf-8").strip().splitlines()
        assert [json.loads(l)["decision"] for l in lines] == \
            ["approve", "reject", "abort"]


# ---------------------------------------------------------------------------
# wire_heal_edges helper produces the same topology as before the refactor
# ---------------------------------------------------------------------------

class TestWireHealEdges:
    def test_default_graph_topology_unchanged(self):
        g = build_graph().compile()
        nodes = set(g.get_graph().nodes)
        assert "simulate_fix" not in nodes
        assert "apply_fix" in nodes

    def test_twin_graph_inserts_simulate_fix(self):
        g = build_graph(twin=True).compile()
        assert "simulate_fix" in set(g.get_graph().nodes)

    def test_orchestrator_default_no_simulate_fix(self):
        g = build_orchestrator_graph().compile()
        assert "simulate_fix" not in set(g.get_graph().nodes)

    def test_orchestrator_twin_inserts_simulate_fix(self):
        g = build_orchestrator_graph(twin=True).compile()
        assert "simulate_fix" in set(g.get_graph().nodes)
