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

from ..domain_classifier import annotate as annotate_domains, sort_by_domain
from ..signal_validator import attach_validation
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
            # Phase 4-I: optional per-scenario device routing
            "device_id": spec.get("device_id", "default"),
            # P0 #3: mock-mode override -- when set, ``execute_scenario``
            # bypasses the evaluator and forces this status. Lets a
            # scenario library smoke-test pipeline plumbing on
            # ``--dut-backend mock`` without expensive Claude analyze
            # cycles on the inevitable mock-zero-stats failure.
            "mock_expected_status": spec.get("mock_expected_status"),
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

        # Pre-check: every referenced signal must exist in the loaded model.
        # Scenarios with missing signals are still included (so they appear in
        # the report) but carry `validation_errors` -- execute_scenario will
        # short-circuit them to ERROR without running stimulus.
        bad_count = attach_validation(predefined, signals)

        # Phase 4-B: tag each scenario with its domain (bms/pcs/grid/general)
        # and reorder so all bms come first, then pcs, then grid, then general.
        # The single-agent graph runs them sequentially; the orchestrator graph
        # uses these tags to route to per-domain subgraph nodes.
        domain_counts = annotate_domains(predefined)
        predefined = sort_by_domain(predefined)
        # Restore monotonic priority after sort so generate_report displays
        # in the same order LangGraph executes.
        for i, s in enumerate(predefined, start=1):
            s["priority"] = i

        # Build standard_coverage from standard_ref fields
        std_cov: dict[str, list[str]] = {}
        for s in predefined:
            ref = s.get("standard_ref", "")
            if ref:
                std_cov.setdefault(ref, []).append(s["scenario_id"])

        first_domain = predefined[0]["domain"] if predefined else "general"
        events = [
            make_event(
                "plan_tests", "plan",
                f"Loaded {len(predefined)} predefined scenarios from {config_path}"
                + " | domains: " + ", ".join(
                    f"{d}={n}" for d, n in domain_counts.items() if n
                ),
                {"scenario_count": len(predefined), "source": "yaml",
                 "validation_failures": bad_count,
                 "domain_counts": dict(domain_counts)},
            )
        ]
        if bad_count:
            bad_ids = [s["scenario_id"] for s in predefined
                       if s.get("validation_errors")]
            events.append(make_event(
                "plan_tests", "warning",
                f"{bad_count} scenario(s) reference unknown signals: "
                f"{', '.join(bad_ids[:5])}"
                + (f" (+{bad_count-5} more)" if bad_count > 5 else ""),
                {"invalid_scenarios": bad_ids},
            ))

        return {
            "plan_strategy": f"Predefined scenarios from {config_path}",
            "scenarios": predefined,
            "scenario_index": 0,
            "estimated_duration_s": (len(predefined) - bad_count) * 30,
            "standard_coverage": std_cov,
            "current_domain": first_domain,
            "domain_counts": dict(domain_counts),
            "events": events,
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
    ).with_config(
        tags=["plan_tests", "claude-sonnet-4"],
        metadata={"node": "plan_tests", "goal": goal[:80]},
        run_name="plan_tests.llm",
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

    # Same signal pre-check as the predefined path -- Claude hallucinates
    # signal names occasionally, so validate before stimulus application.
    bad_count = attach_validation(scenarios, signals)

    # Phase 4-B domain tagging + ordering (mirrors the predefined path).
    domain_counts = annotate_domains(scenarios)
    scenarios = sort_by_domain(scenarios)
    for i, s in enumerate(scenarios, start=1):
        s["priority"] = i
    first_domain = scenarios[0]["domain"] if scenarios else "general"

    events = [
        make_event(
            "plan_tests", "plan",
            f"Plan: {len(scenarios)} scenarios, "
            f"~{plan_data.get('estimated_duration_s', 0)}s"
            + " | domains: " + ", ".join(
                f"{d}={n}" for d, n in domain_counts.items() if n
            ),
            {"scenario_count": len(scenarios),
             "validation_failures": bad_count,
             "domain_counts": dict(domain_counts)},
        )
    ]
    if bad_count:
        bad_ids = [s.get("scenario_id", "?") for s in scenarios
                   if s.get("validation_errors")]
        events.append(make_event(
            "plan_tests", "warning",
            f"{bad_count} Claude-planned scenario(s) reference unknown signals: "
            f"{', '.join(bad_ids[:5])}",
            {"invalid_scenarios": bad_ids},
        ))

    return {
        "plan_strategy": plan_data.get("strategy", ""),
        "scenarios": scenarios,
        "scenario_index": 0,
        "estimated_duration_s": plan_data.get("estimated_duration_s", 0),
        "standard_coverage": plan_data.get("standard_coverage", {}),
        "current_domain": first_domain,
        "domain_counts": dict(domain_counts),
        "events": events,
    }
