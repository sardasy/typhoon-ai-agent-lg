"""
Node-level unit tests for plan_tests, analyze_failure, generate_report.

Claude API is fully mocked — no ANTHROPIC_API_KEY needed.
Run: pytest tests/test_nodes.py -v
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.plan_tests import plan_tests
from src.nodes.analyze_failure import analyze_failure
from src.nodes.generate_report import generate_report
from src.state import AgentState


# ---------------------------------------------------------------------------
# Shared state factory (mirrors test_graph._state)
# ---------------------------------------------------------------------------

def _state(**overrides) -> AgentState:
    base: dict = {
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
# Mock helpers
# ---------------------------------------------------------------------------

VALID_PLAN = {
    "strategy": "BMS protection sequence",
    "scenarios": [
        {
            "scenario_id": "s1",
            "name": "OVP_4V2_100ms",
            "category": "protection",
            "priority": 1,
            "parameters": {"threshold_v": 4.2, "ramp_rate": 0.1},
            "pass_fail_rules": {"max_response_ms": 100},
        },
        {
            "scenario_id": "s2",
            "name": "UVP_2V8_200ms",
            "category": "protection",
            "priority": 2,
            "parameters": {"threshold_v": 2.8},
            "pass_fail_rules": {"max_response_ms": 200},
        },
    ],
    "estimated_duration_s": 120,
    "standard_coverage": {"IEC62619": ["5.4.1"]},
}

VALID_DIAGNOSIS = {
    "failed_scenario_id": "s1",
    "root_cause": {
        "category": "tuning",
        "description": "OVP_DELAY_CAL offset of +5ms detected",
        "confidence": 0.9,
        "evidence": ["Trigger at 105ms, limit 100ms"],
    },
    "corrective_action": {
        "type": "xcp_calibration",
        "parameter": "OVP_DELAY_CAL",
        "suggested_value": 0.095,
    },
}


def _mock_llm(content: str) -> MagicMock:
    """Return a mocked ChatAnthropic that returns content on ainvoke."""
    resp = MagicMock()
    resp.content = content
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


# ---------------------------------------------------------------------------
# plan_tests
# ---------------------------------------------------------------------------

class TestPlanTests:
    async def test_valid_json_parses_scenarios(self):
        with patch("src.nodes.plan_tests.ChatAnthropic", return_value=_mock_llm(json.dumps(VALID_PLAN))):
            result = await plan_tests(_state(goal="BMS OVP test", model_signals=["V_batt"]))

        assert len(result["scenarios"]) == 2
        assert result["scenarios"][0]["scenario_id"] == "s1"
        assert result["events"][0]["event_type"] == "plan"
        assert not result.get("error")

    async def test_scenarios_sorted_by_priority(self):
        # Deliberately reversed priority order in input
        shuffled = {**VALID_PLAN, "scenarios": [
            {**VALID_PLAN["scenarios"][1], "priority": 2},
            {**VALID_PLAN["scenarios"][0], "priority": 1},
        ]}
        with patch("src.nodes.plan_tests.ChatAnthropic", return_value=_mock_llm(json.dumps(shuffled))):
            result = await plan_tests(_state(goal="test"))

        assert result["scenarios"][0]["priority"] == 1
        assert result["scenarios"][1]["priority"] == 2

    async def test_markdown_fences_stripped(self):
        fenced = f"```json\n{json.dumps(VALID_PLAN)}\n```"
        with patch("src.nodes.plan_tests.ChatAnthropic", return_value=_mock_llm(fenced)):
            result = await plan_tests(_state(goal="test"))

        assert len(result["scenarios"]) == 2

    async def test_invalid_json_sets_error(self):
        with patch("src.nodes.plan_tests.ChatAnthropic", return_value=_mock_llm("not json at all")):
            result = await plan_tests(_state(goal="test"))

        assert result.get("error")
        assert result["events"][0]["event_type"] == "error"

    async def test_estimated_duration_stored(self):
        with patch("src.nodes.plan_tests.ChatAnthropic", return_value=_mock_llm(json.dumps(VALID_PLAN))):
            result = await plan_tests(_state(goal="test"))

        assert result["estimated_duration_s"] == 120

    async def test_standard_coverage_stored(self):
        with patch("src.nodes.plan_tests.ChatAnthropic", return_value=_mock_llm(json.dumps(VALID_PLAN))):
            result = await plan_tests(_state(goal="test"))

        assert "IEC62619" in result["standard_coverage"]


# ---------------------------------------------------------------------------
# analyze_failure
# ---------------------------------------------------------------------------

class TestAnalyzeFailure:
    async def test_valid_diagnosis_parsed(self):
        state = _state(
            results=[{"status": "fail", "scenario_id": "s1", "fail_reason": "105ms > 100ms"}],
            current_scenario={"scenario_id": "s1", "name": "OVP_4V2"},
        )
        with patch("src.nodes.analyze_failure.ChatAnthropic", return_value=_mock_llm(json.dumps(VALID_DIAGNOSIS))):
            result = await analyze_failure(state)

        diag = result["diagnosis"]
        assert diag["corrective_action_type"] == "xcp_calibration"
        assert diag["corrective_param"] == "OVP_DELAY_CAL"
        assert diag["corrective_value"] == pytest.approx(0.095)
        assert diag["confidence"] == pytest.approx(0.9)
        assert result["events"][0]["event_type"] == "diagnosis"

    async def test_invalid_json_forces_escalate(self):
        state = _state(
            results=[{"status": "fail", "scenario_id": "s1"}],
            current_scenario={"scenario_id": "s1"},
        )
        with patch("src.nodes.analyze_failure.ChatAnthropic", return_value=_mock_llm("garbage response")):
            result = await analyze_failure(state)

        assert result["diagnosis"]["corrective_action_type"] == "escalate"
        assert result["events"][0]["event_type"] == "error"

    async def test_no_failure_data_returns_error_event(self):
        state = _state(results=[], current_scenario=None)
        result = await analyze_failure(state)

        assert result["events"][0]["event_type"] == "error"
        assert result.get("diagnosis") is None

    async def test_markdown_fences_stripped(self):
        fenced = f"```json\n{json.dumps(VALID_DIAGNOSIS)}\n```"
        state = _state(
            results=[{"status": "fail", "scenario_id": "s1"}],
            current_scenario={"scenario_id": "s1"},
        )
        with patch("src.nodes.analyze_failure.ChatAnthropic", return_value=_mock_llm(fenced)):
            result = await analyze_failure(state)

        assert result["diagnosis"]["corrective_action_type"] == "xcp_calibration"

    async def test_confidence_stored(self):
        state = _state(
            results=[{"status": "fail", "scenario_id": "s1"}],
            current_scenario={"scenario_id": "s1"},
        )
        with patch("src.nodes.analyze_failure.ChatAnthropic", return_value=_mock_llm(json.dumps(VALID_DIAGNOSIS))):
            result = await analyze_failure(state)

        assert result["diagnosis"]["confidence"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def _results(self):
        return [
            {
                "scenario_id": "s1", "status": "pass",
                "duration_s": 5.2, "fail_reason": "",
                "retry_count": 0, "root_cause": "", "waveform_stats": [],
            },
            {
                "scenario_id": "s2", "status": "fail",
                "duration_s": 3.1, "fail_reason": "response 210ms > 200ms",
                "retry_count": 1, "root_cause": "delay_cal_offset", "waveform_stats": [],
            },
        ]

    def _scenarios(self):
        return [
            {"scenario_id": "s1", "name": "OVP_4V2", "category": "protection"},
            {"scenario_id": "s2", "name": "UVP_2V8", "category": "protection"},
        ]

    async def test_html_file_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_hil = MagicMock()
        mock_hil.execute = AsyncMock(return_value={"status": "stopped"})

        with patch("src.nodes.generate_report.get_hil", return_value=mock_hil):
            result = await generate_report(
                _state(goal="BMS OVP", results=self._results(), scenarios=self._scenarios())
            )

        assert result["report_path"] != ""
        assert Path(result["report_path"]).exists()
        assert result["report_path"].endswith(".html")

    async def test_event_type_is_report(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_hil = MagicMock()
        mock_hil.execute = AsyncMock(return_value={"status": "stopped"})

        with patch("src.nodes.generate_report.get_hil", return_value=mock_hil):
            result = await generate_report(
                _state(goal="test", results=self._results(), scenarios=self._scenarios())
            )

        assert result["events"][0]["event_type"] == "report"

    async def test_summary_counts_in_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_hil = MagicMock()
        mock_hil.execute = AsyncMock(return_value={"status": "stopped"})

        with patch("src.nodes.generate_report.get_hil", return_value=mock_hil):
            result = await generate_report(
                _state(goal="test", results=self._results(), scenarios=self._scenarios())
            )

        msg = result["events"][0]["message"]
        assert "Passed: 1" in msg
        assert "Failed: 1" in msg

    async def test_html_contains_goal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_hil = MagicMock()
        mock_hil.execute = AsyncMock(return_value={"status": "stopped"})

        with patch("src.nodes.generate_report.get_hil", return_value=mock_hil):
            result = await generate_report(
                _state(goal="BMS OVP unique goal string", results=self._results(), scenarios=self._scenarios())
            )

        html = Path(result["report_path"]).read_text(encoding="utf-8")
        assert "BMS OVP unique goal string" in html
