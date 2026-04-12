"""
Node: advance_scenario

Increments scenario_index and resets heal_retry_count.
This is a thin routing node — the conditional edge after it
checks if there are more scenarios to run.
"""

from __future__ import annotations

from typing import Any

from ..state import AgentState, make_event


async def advance_scenario(state: AgentState) -> dict[str, Any]:
    """Move to the next scenario."""
    new_idx = state.get("scenario_index", 0) + 1
    total = len(state.get("scenarios", []))

    return {
        "scenario_index": new_idx,
        "heal_retry_count": 0,
        "current_scenario": None,
        "diagnosis": None,
        "events": [
            make_event(
                "advance", "thought",
                f"Moving to scenario {new_idx + 1}/{total}",
            )
        ],
    }
