"""Node: simulate_fix (Phase 4-C).

Runs between ``analyze_failure`` and ``apply_fix`` when the digital twin
is enabled. Asks ``DigitalTwin.predict()`` to vet the analyzer's
proposed corrective action and writes a ``twin_prediction`` to state.

The conditional edge after this node routes:

    commit / uncertain -> apply_fix
    veto               -> advance_scenario  (skip this fix, escalate)

When ``state["twin_enabled"]`` is False the orchestrator never wires
this node into the graph, so this code path is fully opt-in.
"""

from __future__ import annotations

from typing import Any

from ..state import AgentState, make_event
from ..twin import get_twin


async def simulate_fix(state: AgentState) -> dict[str, Any]:
    """Run the twin's what-if check against the proposed fix."""
    diagnosis = state.get("diagnosis") or {}
    scenario = state.get("current_scenario") or {}
    results = state.get("results", [])
    failed_result = results[-1] if results else {}

    twin = get_twin()
    pred = twin.predict(
        scenario=scenario,
        failed_result=failed_result,
        action=diagnosis,
    )

    domain = scenario.get("domain", state.get("current_domain", "general"))
    label = f"twin/{domain}"
    msg_prefix = {
        "commit":    f"[{label}] OK",
        "veto":      f"[{label}] VETO",
        "uncertain": f"[{label}] uncertain",
    }.get(pred.verdict, f"[{label}]")

    return {
        "twin_prediction": pred.to_dict(),
        "events": [make_event(
            "simulate_fix", "thought",
            f"{msg_prefix} {pred.reason}",
            pred.to_dict(),
        )],
    }
