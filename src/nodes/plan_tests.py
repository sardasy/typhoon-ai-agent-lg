"""
Node: plan_tests

Calls Claude (Planner agent) to convert the NL goal + model signals
into a structured JSON test plan with prioritized scenarios.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from ..state import AgentState, make_event

logger = logging.getLogger(__name__)

PLANNER_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "planner.md"


async def plan_tests(state: AgentState) -> dict[str, Any]:
    """Generate test plan from goal + model info via Claude."""

    goal = state["goal"]
    signals = state.get("model_signals", [])
    rag_context = state.get("rag_context", "")

    # Load planner system prompt
    system_prompt = PLANNER_PROMPT_PATH.read_text(encoding="utf-8")

    # Build user message
    user_msg = (
        f"## User goal\n{goal}\n\n"
        f"## Model signals\n{json.dumps(signals[:50])}\n\n"
    )
    if rag_context:
        user_msg += f"## Standards / knowledge context\n{rag_context}\n"

    # Call Claude
    llm = ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=0,
        max_tokens=4096,
    ).with_config(
        tags=["plan_tests", "claude-sonnet-4"],
        metadata={"node": "plan_tests", "goal": goal[:80]},
        run_name="plan_tests.llm",
    )
    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

    # Parse JSON plan
    raw = response.content.strip()
    clean = raw.removeprefix("```json").removesuffix("```").strip()

    try:
        plan_data = json.loads(clean)
    except json.JSONDecodeError:
        logger.error(f"Planner returned invalid JSON:\n{raw[:300]}")
        return {
            "error": "Planner failed to produce valid JSON",
            "events": [make_event("plan_tests", "error", "Invalid JSON from planner")],
        }

    scenarios = plan_data.get("scenarios", [])
    # Sort by priority
    scenarios.sort(key=lambda s: s.get("priority", 99))

    return {
        "plan_strategy": plan_data.get("strategy", ""),
        "scenarios": scenarios,
        "scenario_index": 0,
        "estimated_duration_s": plan_data.get("estimated_duration_s", 0),
        "standard_coverage": plan_data.get("standard_coverage", {}),
        "events": [
            make_event(
                "plan_tests", "plan",
                f"Plan: {len(scenarios)} scenarios, ~{plan_data.get('estimated_duration_s', 0)}s",
                {"scenario_count": len(scenarios)},
            )
        ],
    }
