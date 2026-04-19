"""
Tests for THAA LangGraph edition.

Focus: graph routing logic, state transitions, node contracts.
Run: pytest tests/ -v
"""

from __future__ import annotations

import pytest

from src.state import AgentState, ScenarioResult, make_event
from src.graph import (
    route_after_exec,
    route_after_analysis,
    route_has_more,
    build_graph,
    MAX_HEAL_RETRIES,
)
from src.validator import Validator, SafetyConfig
from src.tools.hil_tools import HILToolExecutor
from src.tools.xcp_tools import XCPToolExecutor
from src.tools.rag_tools import RAGToolExecutor


# ---------------------------------------------------------------------------
# Helper: minimal state factory
# ---------------------------------------------------------------------------

def _state(**overrides) -> AgentState:
    base = {
        "goal": "test",
        "config_path": "configs/model.yaml",
        "model_path": "",
        "model_signals": [],
        "model_loaded": False,
        "device_mode": "",
        "active_preset": "",
        "rag_context": "",
        "plan_strategy": "",
        "scenarios": [],
        "scenario_index": 0,
        "estimated_duration_s": 0,
        "standard_coverage": {},
        "results": [],
        "current_scenario": None,
        "diagnosis": None,
        "heal_retry_count": 0,
        "events": [],
        "report_path": "",
        "error": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Routing: route_after_exec
# ---------------------------------------------------------------------------

class TestRouteAfterExec:
    def test_pass_with_more_scenarios(self):
        state = _state(
            scenarios=[{"scenario_id": "s1"}, {"scenario_id": "s2"}],
            scenario_index=0,
            results=[{"status": "pass", "scenario_id": "s1"}],
        )
        assert route_after_exec(state) == "next"

    def test_pass_last_scenario(self):
        state = _state(
            scenarios=[{"scenario_id": "s1"}],
            scenario_index=0,
            results=[{"status": "pass", "scenario_id": "s1"}],
        )
        assert route_after_exec(state) == "done"

    def test_fail_triggers_analysis(self):
        state = _state(
            scenarios=[{"scenario_id": "s1"}, {"scenario_id": "s2"}],
            scenario_index=0,
            heal_retry_count=0,
            results=[{"status": "fail", "scenario_id": "s1"}],
        )
        assert route_after_exec(state) == "fail"

    def test_fail_max_retries_moves_on(self):
        state = _state(
            scenarios=[{"scenario_id": "s1"}, {"scenario_id": "s2"}],
            scenario_index=0,
            heal_retry_count=MAX_HEAL_RETRIES,
            results=[{"status": "fail", "scenario_id": "s1"}],
        )
        assert route_after_exec(state) == "next"

    def test_fail_max_retries_last_scenario(self):
        state = _state(
            scenarios=[{"scenario_id": "s1"}],
            scenario_index=0,
            heal_retry_count=MAX_HEAL_RETRIES,
            results=[{"status": "fail", "scenario_id": "s1"}],
        )
        assert route_after_exec(state) == "done"

    def test_empty_results(self):
        state = _state(results=[])
        assert route_after_exec(state) == "done"

    def test_error_status_moves_on(self):
        state = _state(
            scenarios=[{"scenario_id": "s1"}, {"scenario_id": "s2"}],
            scenario_index=0,
            results=[{"status": "error", "scenario_id": "s1"}],
        )
        assert route_after_exec(state) == "next"


# ---------------------------------------------------------------------------
# Routing: route_after_analysis
# ---------------------------------------------------------------------------

class TestRouteAfterAnalysis:
    def test_fixable_retries(self):
        state = _state(
            diagnosis={
                "corrective_action_type": "xcp_calibration",
                "confidence": 0.9,
            },
            heal_retry_count=0,
        )
        assert route_after_analysis(state) == "retry"

    def test_low_confidence_escalates(self):
        state = _state(
            diagnosis={
                "corrective_action_type": "xcp_calibration",
                "confidence": 0.3,
            },
            heal_retry_count=0,
        )
        assert route_after_analysis(state) == "escalate"

    def test_escalate_action_escalates(self):
        state = _state(
            diagnosis={
                "corrective_action_type": "escalate",
                "confidence": 0.9,
            },
        )
        assert route_after_analysis(state) == "escalate"

    def test_max_retries_escalates(self):
        state = _state(
            diagnosis={
                "corrective_action_type": "xcp_calibration",
                "confidence": 0.9,
            },
            heal_retry_count=MAX_HEAL_RETRIES,
        )
        assert route_after_analysis(state) == "escalate"

    def test_no_diagnosis_escalates(self):
        state = _state(diagnosis=None)
        assert route_after_analysis(state) == "escalate"


# ---------------------------------------------------------------------------
# Routing: route_has_more
# ---------------------------------------------------------------------------

class TestRouteHasMore:
    def test_more_remaining(self):
        state = _state(
            scenarios=[{"id": "1"}, {"id": "2"}, {"id": "3"}],
            scenario_index=1,
        )
        assert route_has_more(state) == "yes"

    def test_at_end(self):
        state = _state(
            scenarios=[{"id": "1"}, {"id": "2"}],
            scenario_index=2,
        )
        assert route_has_more(state) == "no"

    def test_empty_scenarios(self):
        state = _state(scenarios=[], scenario_index=0)
        assert route_has_more(state) == "no"


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

class TestGraphStructure:
    def test_graph_compiles(self):
        g = build_graph()
        compiled = g.compile()
        assert compiled is not None

    def test_graph_has_all_nodes(self):
        g = build_graph()
        compiled = g.compile()
        graph_repr = compiled.get_graph()
        node_ids = set(graph_repr.nodes)
        expected = {
            "load_model", "plan_tests", "execute_scenario",
            "analyze_failure", "apply_fix", "advance_scenario",
            "generate_report", "__start__", "__end__",
        }
        assert expected.issubset(node_ids), f"Missing: {expected - node_ids}"

    def test_entry_point_is_load_model(self):
        g = build_graph()
        compiled = g.compile()
        graph_repr = compiled.get_graph()
        start_edges = [
            e for e in graph_repr.edges
            if e.source == "__start__"
        ]
        assert len(start_edges) == 1
        assert start_edges[0].target == "load_model"

    def test_generate_report_goes_to_end(self):
        g = build_graph()
        compiled = g.compile()
        graph_repr = compiled.get_graph()
        report_edges = [
            e for e in graph_repr.edges
            if e.source == "generate_report"
        ]
        targets = {e.target for e in report_edges}
        assert "__end__" in targets


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

class TestStateHelpers:
    def test_make_event(self):
        ev = make_event("test_node", "action", "hello", {"key": "val"})
        assert ev["node"] == "test_node"
        assert ev["event_type"] == "action"
        assert ev["message"] == "hello"
        assert ev["data"]["key"] == "val"
        assert "timestamp" in ev


# ---------------------------------------------------------------------------
# Validator (reused from v1)
# ---------------------------------------------------------------------------

class TestValidator:
    def setup_method(self):
        self.v = Validator(SafetyConfig(max_voltage=60.0, max_fault_injections=3))

    def test_voltage_ok(self):
        assert self.v.validate("hil_signal_write", {"value": 50}).allowed

    def test_voltage_blocked(self):
        r = self.v.validate("hil_signal_write", {"value": 100})
        assert not r.allowed

    def test_xcp_whitelist_ok(self):
        assert self.v.validate("xcp_interface", {
            "action": "write", "variable": "Ctrl_Kp", "value": 0.5,
        }).allowed

    def test_xcp_whitelist_blocked(self):
        r = self.v.validate("xcp_interface", {
            "action": "write", "variable": "DANGEROUS", "value": 5,
        })
        assert not r.allowed


# ---------------------------------------------------------------------------
# Tool executors (mock mode)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    __import__("src.tools.hil_tools", fromlist=["HAS_TYPHOON"]).HAS_TYPHOON,
    reason="Mock-mode behavior tests; skip when real Typhoon API is installed",
)
class TestHILToolsMock:
    @pytest.fixture
    def hil(self):
        return HILToolExecutor()

    @pytest.mark.asyncio
    async def test_load(self, hil):
        r = await hil.execute("hil_control", {"action": "load", "model_path": "t.tse"})
        assert r["status"] == "model_loaded"

    @pytest.mark.asyncio
    async def test_capture(self, hil):
        await hil.execute("hil_control", {"action": "load", "model_path": "t.tse"})
        r = await hil.execute("hil_capture", {
            "signals": ["V_cell_1"], "duration_s": 0.1,
        })
        assert "statistics" in r


class TestXCPToolsMock:
    @pytest.mark.asyncio
    async def test_write_whitelisted(self):
        xcp = XCPToolExecutor()
        await xcp.execute("xcp_interface", {"action": "connect", "a2l_path": "t.a2l"})
        r = await xcp.execute("xcp_interface", {
            "action": "write", "variable": "Ctrl_Kp", "value": 0.3,
        })
        assert r["status"] == "ok"

    @pytest.mark.asyncio
    async def test_write_blocked(self):
        xcp = XCPToolExecutor()
        await xcp.execute("xcp_interface", {"action": "connect", "a2l_path": "t.a2l"})
        r = await xcp.execute("xcp_interface", {
            "action": "write", "variable": "FORBIDDEN", "value": 1,
        })
        assert r.get("blocked") is True


class TestRAGToolsMock:
    @pytest.mark.asyncio
    async def test_query(self):
        rag = RAGToolExecutor()
        r = await rag.execute("rag_query", {
            "query": "overvoltage IEC",
            "sources": ["standards"],
        })
        assert len(r["results"]) > 0
