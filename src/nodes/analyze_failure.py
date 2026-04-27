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

from ..domain_classifier import overlay_for
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
    sid = scenario.get("scenario_id", "")

    # P0 #4: cost guard -- consult the on-disk diagnosis cache first
    # (saves Claude tokens on identical re-failures across runs), and
    # short-circuit to ``escalate`` once the per-run hard cap trips.
    from ..cost_guard import (
        consume_one_call, lookup_cached_diagnosis,
        record_cached_diagnosis, synthetic_escalate_diagnosis,
    )
    cached = lookup_cached_diagnosis(sid, failed_result)
    if cached is not None:
        return {
            "diagnosis": cached,
            "events": [make_event(
                "analyze", "diagnosis",
                f"Cached diagnosis hit for {sid} -- skipping Claude call",
                cached,
            )],
        }
    if not consume_one_call():
        synth = synthetic_escalate_diagnosis(
            sid,
            "Claude per-run call cap reached (THAA_MAX_CLAUDE_CALLS_PER_RUN). "
            "Escalating remaining failures.",
        )
        return {
            "diagnosis": synth,
            "events": [make_event(
                "analyze", "error",
                "Cost guard tripped: synthetic escalate diagnosis emitted",
                synth,
            )],
        }

    # Load analyzer prompt + Phase 4-B domain overlay (BMS / PCS / Grid).
    base_prompt = ANALYZER_PROMPT_PATH.read_text(encoding="utf-8")
    domain = scenario.get("domain") or state.get("current_domain") or "general"
    overlay = overlay_for(domain)
    system_prompt = f"{base_prompt}\n\n{overlay}".rstrip() if overlay else base_prompt

    # P1 #7: inject the live XCP whitelist so the analyzer never proposes
    # a corrective_param outside the allowed set. Saves Claude tokens
    # (no more BLOCKED retries on proposals like ``BMS_contactorDelay_ms``).
    from ..validator import WRITABLE_XCP_PARAMS
    system_prompt += (
        "\n\n## Calibration whitelist (HARD CONSTRAINT)\n"
        "When proposing ``corrective_action.parameter``, you MUST choose\n"
        "from this exact set. Any other parameter will be rejected by the\n"
        "Validator and the heal retry will be wasted:\n"
        + ", ".join(sorted(WRITABLE_XCP_PARAMS))
    )

    # Build context
    user_msg = (
        f"## Failed scenario\n{json.dumps(scenario, indent=2, ensure_ascii=False)}\n\n"
        f"## Test result\n{json.dumps(failed_result, indent=2, ensure_ascii=False)}\n\n"
    )
    # Phase 4-G: prefer the failed scenario's domain namespace when
    # available; fall back to the global pull.
    by_domain = state.get("rag_context_by_domain") or {}
    rag_ctx = by_domain.get(domain) or state.get("rag_context", "")
    if rag_ctx:
        domain_tag = f" ({domain})" if by_domain.get(domain) else ""
        user_msg += f"## Past test history / standards{domain_tag}\n{rag_ctx}\n"

    # Call Claude
    llm = ChatAnthropic(
        model="claude-sonnet-4-20250514",
        temperature=0,
        max_tokens=4096,
    ).with_config(
        tags=["analyze_failure", "claude-sonnet-4", f"agent:{domain}"],
        metadata={
            "node": "analyze_failure",
            "scenario_id": scenario.get("scenario_id", ""),
            "domain": domain,
        },
        run_name=f"analyze_failure.{domain}.llm",
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

    # P0 #4: persist to the cache so identical re-failures next run
    # skip Claude entirely.
    record_cached_diagnosis(sid, failed_result, diagnosis)

    return {
        "diagnosis": diagnosis,
        "events": [
            make_event(
                "analyze", "diagnosis",
                f"[{domain}_agent] Root cause: {desc} (confidence={conf:.0%})",
                {**diagnosis, "domain": domain},
            )
        ],
    }
