"""Tests for analyze_failure / plan_tests with a fake LLM injected.

The two LLM-driven nodes were previously untested -- their unit tests
required ANTHROPIC_API_KEY. Here we ``monkeypatch`` ``ChatAnthropic``
to return canned JSON responses, so prompt-handling logic, JSON
parsing, and error fallbacks are all exercised offline.

Pattern callers can reuse:

    fake = FakeLLMResponse('{"...": "..."}')
    monkeypatch.setattr(<module>, "ChatAnthropic", lambda **_: fake)

The fake honors the same surface ``analyze_failure`` and ``plan_tests``
use: ``with_config().ainvoke([SystemMessage, HumanMessage]) -> obj``
where ``obj.content`` is the canned response.
"""

from __future__ import annotations

import json
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake ChatAnthropic that captures the prompt and returns canned content
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    """Minimal stand-in for ChatAnthropic used by analyze_failure / plan_tests.

    Captures the last (system_prompt, user_message) pair so tests can
    assert that the prompt was constructed correctly. Returns whatever
    string is configured at construction time.
    """

    def __init__(self, content: str = "{}") -> None:
        self.content = content
        self.calls: list[tuple[str, str]] = []
        self._tags: list[str] = []
        self._metadata: dict = {}

    def with_config(self, *_, tags=None, metadata=None, run_name=None,
                     **__):
        if tags:
            self._tags = list(tags)
        if metadata:
            self._metadata = dict(metadata)
        return self

    async def ainvoke(self, messages: list[Any]) -> _FakeResponse:
        sys_msg = next(
            (m.content for m in messages if type(m).__name__ == "SystemMessage"),
            "",
        )
        usr_msg = next(
            (m.content for m in messages if type(m).__name__ == "HumanMessage"),
            "",
        )
        self.calls.append((sys_msg, usr_msg))
        return _FakeResponse(self.content)


# ---------------------------------------------------------------------------
# analyze_failure
# ---------------------------------------------------------------------------

class TestAnalyzeFailure:
    @pytest.mark.asyncio
    async def test_returns_normalized_diagnosis(self, monkeypatch):
        canned = json.dumps({
            "failed_scenario_id": "vsm_demo",
            "root_cause": {
                "category": "tuning",
                "description": "VSM inertia too low",
                "confidence": 0.92,
                "evidence": ["relay_max=0", "rise_time_ms=null"],
            },
            "corrective_action": {
                "type": "xcp_calibration",
                "parameter": "J",
                "suggested_value": 0.35,
            },
        })
        fake = FakeLLM(canned)
        from src.nodes import analyze_failure as af_mod
        monkeypatch.setattr(af_mod, "ChatAnthropic", lambda **_: fake)

        out = await af_mod.analyze_failure({
            "current_scenario": {
                "scenario_id": "vsm_demo", "domain": "grid",
                "pass_fail_rules": {"relay_must_trip": True},
            },
            "results": [{"status": "fail", "fail_reason": "no trip",
                         "scenario_id": "vsm_demo"}],
            "current_domain": "grid",
        })
        diag = out["diagnosis"]
        assert diag["root_cause_description"] == "VSM inertia too low"
        assert diag["corrective_param"] == "J"
        assert diag["corrective_value"] == 0.35
        assert diag["confidence"] == pytest.approx(0.92)

    @pytest.mark.asyncio
    async def test_grid_domain_overlay_applied_to_prompt(self, monkeypatch):
        fake = FakeLLM(json.dumps({"root_cause": {}, "corrective_action": {}}))
        from src.nodes import analyze_failure as af_mod
        monkeypatch.setattr(af_mod, "ChatAnthropic", lambda **_: fake)

        await af_mod.analyze_failure({
            "current_scenario": {"scenario_id": "x", "domain": "grid",
                                 "pass_fail_rules": {}},
            "results": [{"status": "fail", "scenario_id": "x"}],
            "current_domain": "grid",
        })
        assert fake.calls, "fake LLM was never invoked"
        sys_msg, _ = fake.calls[-1]
        # The grid overlay text must have been concatenated onto the prompt.
        assert "Grid Agent" in sys_msg or "grid-tied" in sys_msg.lower()

    @pytest.mark.asyncio
    async def test_invalid_json_falls_back_to_escalate(self, monkeypatch):
        fake = FakeLLM("not valid json {{")
        from src.nodes import analyze_failure as af_mod
        monkeypatch.setattr(af_mod, "ChatAnthropic", lambda **_: fake)

        out = await af_mod.analyze_failure({
            "current_scenario": {"scenario_id": "x", "domain": "general",
                                 "pass_fail_rules": {}},
            "results": [{"status": "fail", "scenario_id": "x"}],
            "current_domain": "general",
        })
        diag = out["diagnosis"]
        assert diag["corrective_action_type"] == "escalate"
        assert any("Invalid JSON" in e["message"] for e in out["events"])

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self, monkeypatch):
        canned = (
            "```json\n"
            + json.dumps({"root_cause": {"description": "ok"},
                          "corrective_action": {"type": "retest"}})
            + "\n```"
        )
        fake = FakeLLM(canned)
        from src.nodes import analyze_failure as af_mod
        monkeypatch.setattr(af_mod, "ChatAnthropic", lambda **_: fake)

        out = await af_mod.analyze_failure({
            "current_scenario": {"scenario_id": "x", "domain": "general",
                                 "pass_fail_rules": {}},
            "results": [{"status": "fail", "scenario_id": "x"}],
            "current_domain": "general",
        })
        assert out["diagnosis"]["root_cause_description"] == "ok"


# ---------------------------------------------------------------------------
# plan_tests (Claude path -- predefined-YAML path is exercised elsewhere)
# ---------------------------------------------------------------------------

class TestPlanTestsClaudePath:
    @pytest.mark.asyncio
    async def test_planner_produces_classified_scenarios(
        self, monkeypatch, tmp_path,
    ):
        canned = json.dumps({
            "strategy": "Run BMS protection sweep then a grid LVRT check.",
            "estimated_duration_s": 60,
            "standard_coverage": {"IEC 62619": ["s1"], "IEEE 1547": ["s2"]},
            "scenarios": [
                {
                    "scenario_id": "s1", "name": "BMS OVP",
                    "description": "OVP", "category": "protection",
                    "priority": 2, "standard_ref": "IEC 62619",
                    "parameters": {"target_cell": 1, "test_voltage": 4.3},
                    "measurements": ["V_cell_1"],
                    "pass_fail_rules": {},
                },
                {
                    "scenario_id": "s2", "name": "Grid LVRT",
                    "description": "LVRT", "category": "frt",
                    "priority": 1, "standard_ref": "IEEE 1547",
                    "parameters": {"fault_template": "voltage_sag"},
                    "measurements": ["Vgrid"],
                    "pass_fail_rules": {},
                },
            ],
        })
        fake = FakeLLM(canned)
        from src.nodes import plan_tests as pt_mod
        monkeypatch.setattr(pt_mod, "ChatAnthropic", lambda **_: fake)

        # Use a config with NO scenarios section so the Claude path runs.
        empty = tmp_path / "model.yaml"
        empty.write_text("model:\n  path: dummy.tse\n", encoding="utf-8")

        out = await pt_mod.plan_tests({
            "goal": "BMS + grid sweep",
            "config_path": str(empty),
            "model_signals": ["V_cell_1", "Vgrid"],
            "rag_context": "",
        })
        scenarios = out["scenarios"]
        # Domain classification ran -- both are tagged.
        domains = sorted(s["domain"] for s in scenarios)
        assert domains == ["bms", "grid"]
        # Sorted by domain (bms -> grid -> ...) and reset priority.
        assert scenarios[0]["domain"] == "bms"
        assert scenarios[1]["domain"] == "grid"
        # Domain counts populated for the orchestrator dispatcher.
        assert out["domain_counts"]["bms"] == 1
        assert out["domain_counts"]["grid"] == 1

    @pytest.mark.asyncio
    async def test_planner_invalid_json_records_error_state(
        self, monkeypatch, tmp_path,
    ):
        fake = FakeLLM("not json")
        from src.nodes import plan_tests as pt_mod
        monkeypatch.setattr(pt_mod, "ChatAnthropic", lambda **_: fake)

        cfg = tmp_path / "m.yaml"
        cfg.write_text("model:\n  path: dummy.tse\n", encoding="utf-8")

        out = await pt_mod.plan_tests({
            "goal": "x", "config_path": str(cfg),
            "model_signals": [], "rag_context": "",
        })
        assert "error" in out
        assert "Planner" in out["error"]
