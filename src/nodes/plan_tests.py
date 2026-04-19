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

import yaml

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from ..state import AgentState, make_event

logger = logging.getLogger(__name__)

PLANNER_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "planner.md"


def _load_predefined_scenarios(config_path: str) -> list[dict[str, Any]]:
    """Load predefined scenarios from config YAML if available.

    Returns a list of scenario dicts ready for the graph, or an empty
    list if the config does not contain a ``scenarios`` section.
    """
    p = Path(config_path)
    if not p.exists():
        return []
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []

    raw_scenarios = cfg.get("scenarios")
    if not raw_scenarios or not isinstance(raw_scenarios, dict):
        return []

    scenarios: list[dict[str, Any]] = []
    priority = 1
    for sid, spec in raw_scenarios.items():
        if not isinstance(spec, dict):
            continue
        scenarios.append({
            "scenario_id": sid,
            "name": spec.get("description", sid),
            "description": spec.get("description", ""),
            "category": spec.get("category", "protection"),
            "priority": priority,
            "standard_ref": spec.get("standard_ref", ""),
            "parameters": spec.get("parameters", {}),
            "measurements": spec.get("measurements", []),
            "pass_fail_rules": spec.get("pass_fail_rules", {}),
        })
        priority += 1

    return scenarios


async def plan_tests(state: AgentState) -> dict[str, Any]:
    """Generate test plan from goal + model info via Claude.

    If the config YAML contains a ``scenarios`` section with predefined
    scenarios, those are loaded directly (no Claude API call needed).
    Otherwise, Claude Planner generates scenarios from the NL goal.
    """

    goal = state["goal"]
    signals = state.get("model_signals", [])
    rag_context = state.get("rag_context", "")
    config_path = state.get("config_path", "configs/model.yaml")

    # Try loading predefined scenarios from YAML first
    predefined = _load_predefined_scenarios(config_path)
    if predefined:
        logger.info(
            "Loaded %d predefined scenarios from %s (skipping Claude Planner)",
            len(predefined), config_path,
        )

        # Build standard_coverage from standard_ref fields
        std_cov: dict[str, list[str]] = {}
        for s in predefined:
            ref = s.get("standard_ref", "")
            if ref:
                std_cov.setdefault(ref, []).append(s["scenario_id"])

        return {
            "plan_strategy": f"Predefined scenarios from {config_path}",
            "scenarios": predefined,
            "scenario_index": 0,
            "estimated_duration_s": len(predefined) * 30,
            "standard_coverage": std_cov,
            "events": [
                make_event(
                    "plan_tests", "plan",
                    f"Loaded {len(predefined)} predefined scenarios from {config_path}",
                    {"scenario_count": len(predefined), "source": "yaml"},
                )
            ],
        }

    # Fallback: call Claude Planner for dynamic scenario generation
    system_prompt = PLANNER_PROMPT_PATH.read_text(encoding="utf-8")

    user_msg = (
        f"## User goal\n{goal}\n\n"
        f"## Model signals\n{json.dumps(signals[:50])}\n\n"
    )
    if rag_context:
        user_msg += f"## Standards / knowledge context\n{rag_context}\n"

    llm = ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=0,
        max_tokens=4096,
    )
    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_msg),
    ])

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
