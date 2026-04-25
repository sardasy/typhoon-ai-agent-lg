"""
Node: advance_scenario

Increments scenario_index and resets heal_retry_count.
This is a thin routing node — the conditional edge after it
checks if there are more scenarios to run.

Phase 4-B: also surfaces domain transitions. When the scenario at the
new index belongs to a different domain than the previous one, emits
a "Switching to <domain> agent" event so multi-agent runs are visible
in the SSE / CLI stream.
"""

from __future__ import annotations

from typing import Any

from ..state import AgentState, make_event


async def advance_scenario(state: AgentState) -> dict[str, Any]:
    """Move to the next scenario."""
    new_idx = state.get("scenario_index", 0) + 1
    scenarios = state.get("scenarios", [])
    total = len(scenarios)

    prev_domain = state.get("current_domain", "")
    next_domain = prev_domain
    if new_idx < total:
        next_domain = scenarios[new_idx].get("domain", "general")

    events = [
        make_event(
            "advance", "thought",
            f"Moving to scenario {new_idx + 1}/{total}",
        )
    ]
    if prev_domain and next_domain and next_domain != prev_domain:
        events.append(make_event(
            "advance", "thought",
            f"Switching agent: {prev_domain} -> {next_domain}",
            {"from": prev_domain, "to": next_domain},
        ))

    return {
        "scenario_index": new_idx,
        "heal_retry_count": 0,
        "current_scenario": None,
        "diagnosis": None,
        "current_domain": next_domain,
        "events": events,
    }
