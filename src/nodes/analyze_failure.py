"""
Node: analyze_failure

Called when a scenario fails. Sends failure data + ECU state + RAG context
to Claude Analyzer, which returns a structured diagnosis.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from ..state import AgentState, DiagnosisResult, make_event

logger = logging.getLogger(__name__)

ANALYZER_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "analyzer.md"


async def analyze_failure(state: AgentState) -> dict[str, Any]:
    """Diagnose the most recent failure via Claude Analyzer."""

    results = state.get("results", [])
    scenario = state.get("current_scenario")

    if not results or not scenario:
        return {"events": [make_event("analyze", "error", "No failure data to analyze")]}

    # The last result is the failed one
    failed_result = results[-1]

    # Load analyzer prompt
    system_prompt = ANALYZER_PROMPT_PATH.read_text(encoding="utf-8")

    # Build context
    user_msg = (
        f"## Failed scenario\n{json.dumps(scenario, indent=2, ensure_ascii=False)}\n\n"
        f"## Test result\n{json.dumps(failed_result, indent=2, ensure_ascii=False)}\n\n"
    )
    rag_ctx = state.get("rag_context", "")
    if rag_ctx:
        user_msg += f"## Past test history / standards\n{rag_ctx}\n"

    # Call Claude
    llm = ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=0,
        max_tokens=4096,
    ).with_config(
        tags=["analyze_failure", "claude-sonnet-4"],
        metadata={
            "node": "analyze_failure",
            "scenario_id": scenario.get("scenario_id", ""),
        },
        run_name="analyze_failure.llm",
    )
    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

    raw = response.content.strip()
    clean = raw.removeprefix("```json").removesuffix("```").strip()

    try:
        diag_data = json.loads(clean)
    except json.JSONDecodeError:
        logger.error(f"Analyzer returned invalid JSON:\n{raw[:300]}")
        return {
            "diagnosis": {
                "failed_scenario_id": scenario.get("scenario_id", ""),
                "root_cause_description": "Analysis failed -- could not parse response",
                "corrective_action_type": "escalate",
            },
            "events": [make_event("analyze", "error", "Invalid JSON from analyzer")],
        }

    # Normalize into DiagnosisResult shape
    diagnosis = {
        "failed_scenario_id": diag_data.get("failed_scenario_id", scenario.get("scenario_id", "")),
        "root_cause_category": diag_data.get("root_cause", {}).get("category", "unknown"),
        "root_cause_description": diag_data.get("root_cause", {}).get("description", ""),
        "confidence": diag_data.get("root_cause", {}).get("confidence", 0.5),
        "corrective_action_type": diag_data.get("corrective_action", {}).get("type", "escalate"),
        "corrective_param": diag_data.get("corrective_action", {}).get("parameter", ""),
        "corrective_value": diag_data.get("corrective_action", {}).get("suggested_value"),
        "evidence": diag_data.get("root_cause", {}).get("evidence", []),
    }

    desc = diagnosis["root_cause_description"]
    conf = diagnosis["confidence"]

    return {
        "diagnosis": diagnosis,
        "events": [
            make_event(
                "analyze", "diagnosis",
                f"Root cause: {desc} (confidence={conf:.0%})",
                diagnosis,
            )
        ],
    }
