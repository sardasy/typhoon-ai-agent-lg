"""
Tests for human-in-the-loop graph compilation.

Verifies that compile_graph(hitl=True) installs the checkpointer +
interrupt_before correctly, and that the graph actually pauses before
``apply_fix`` so an operator can approve / reject the proposed fix.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.graph import build_graph, compile_graph


class TestCompileGraphHITL:
    def test_default_no_interrupts(self):
        """Default compile produces no interrupts (back-compat)."""
        app = compile_graph()
        # Underlying compiled graph stores interrupt_before in `interrupt_before_nodes`
        # (LangGraph internals). Easiest portable check: no checkpointer attached.
        assert getattr(app, "checkpointer", None) is None

    def test_hitl_attaches_checkpointer(self):
        """compile_graph(hitl=True) attaches a MemorySaver checkpointer."""
        app = compile_graph(hitl=True)
        assert app.checkpointer is not None

    def test_env_var_enables_hitl(self):
        with patch.dict(os.environ, {"THAA_HITL": "1"}):
            app = compile_graph()
            assert app.checkpointer is not None

    def test_env_var_disabled_when_false(self):
        with patch.dict(os.environ, {"THAA_HITL": "0"}, clear=False):
            os.environ.pop("THAA_HITL", None)
            os.environ["THAA_HITL"] = "0"
            app = compile_graph()
            assert getattr(app, "checkpointer", None) is None

    def test_custom_interrupt_nodes(self):
        """We can interrupt before any node, not just apply_fix."""
        app = compile_graph(hitl=True, interrupt_nodes=("execute_scenario",))
        assert app.checkpointer is not None
        # Verify by inspecting the compiled graph's interrupt list
        interrupts = getattr(app, "interrupt_before_nodes",
                              getattr(app, "_interrupt_before", set()))
        assert "execute_scenario" in interrupts


class TestHITLInterruptFires:
    """Run the heal_demo scenario in HITL mode and check the graph pauses."""

    @pytest.mark.asyncio
    async def test_pauses_before_apply_fix(self):
        # Build graph in HITL, but stub the analyzer + executor so we can run
        # without an ANTHROPIC_API_KEY.
        from src.state import AgentState
        import src.nodes.analyze_failure as af_mod
        import src.nodes.execute_scenario as ex_mod

        async def fake_execute(state):
            return {
                "current_scenario": {"scenario_id": "fake", "name": "fake"},
                "results": [{
                    "scenario_id": "fake", "status": "fail",
                    "duration_s": 0.1, "fail_reason": "mock fail",
                    "retry_count": 0, "waveform_stats": [],
                }],
                "events": [],
            }

        async def fake_analyze(state):
            return {
                "diagnosis": {
                    "failed_scenario_id": "fake",
                    "root_cause_description": "mock cause",
                    "confidence": 0.9,
                    "corrective_action_type": "xcp_calibration",
                    "corrective_param": "J",
                    "corrective_value": 0.3,
                },
                "events": [],
            }

        async def fake_load(state):
            return {"model_loaded": True, "model_signals": [], "events": []}

        async def fake_plan(state):
            return {
                "scenarios": [{"scenario_id": "fake", "name": "fake",
                               "parameters": {}, "measurements": [],
                               "pass_fail_rules": {}}],
                "scenario_index": 0,
                "events": [],
            }

        with patch("src.graph.load_model", fake_load), \
             patch("src.graph.plan_tests", fake_plan), \
             patch("src.graph.execute_scenario", fake_execute), \
             patch("src.graph.analyze_failure", fake_analyze):
            app = compile_graph(hitl=True)
            cfg = {"configurable": {"thread_id": "test-hitl-1"}}

            init = {
                "goal": "test", "config_path": "x.yaml", "model_path": "",
                "model_signals": [], "model_loaded": False, "rag_context": "",
                "plan_strategy": "", "scenarios": [], "scenario_index": 0,
                "estimated_duration_s": 0, "standard_coverage": {},
                "results": [], "current_scenario": None, "diagnosis": None,
                "heal_retry_count": 0, "events": [], "report_path": "",
                "error": "", "device_mode": "", "active_preset": "",
                "tse_content": "", "tse_path": "", "parsed_tse": None,
                "test_requirements": [], "generated_files": {},
                "codegen_validation": None, "export_path": "",
                "codegen_mode": "mock",
            }

            # Stream until interrupt
            async for _ in app.astream(init, config=cfg):
                pass

            snapshot = app.get_state(cfg)
            # We should be paused right before apply_fix
            assert "apply_fix" in snapshot.next, (
                f"Expected pause before apply_fix, got next={snapshot.next}"
            )
            # Diagnosis should be available for human review
            assert snapshot.values.get("diagnosis", {}).get("corrective_param") == "J"
