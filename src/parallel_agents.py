"""Parallel domain agents (Phase 4-F).

Each domain (BMS / PCS / Grid / General) runs as an independent
asyncio task fanned out from the orchestrator via LangGraph's
:class:`Send` API. Workers process their slice of scenarios in
parallel; the shared HIL hardware is serialized inside the DUT
backends via :data:`src.tools.dut.base.HARDWARE_LOCK`. Claude API
calls (planner / analyzer) are NOT lock-bound, so failures across
different domains can be diagnosed concurrently -- the headline win
of the parallel mode.

Why workers are plain async functions, not compiled subgraphs:
LangGraph composes parent + subgraph state via shared channels, so
``Annotated[list, operator.add]`` fields (events, results) get
double-counted across the boundary (we hit this in Phase 4-B).
Workers run the heal loop directly in Python, returning a single
state delta to the parent. Each worker is invoked via :class:`Send`
with a per-domain *branch state* (the worker's scenario subset);
``operator.add`` reducers on ``results`` and ``events`` merge the
branches' deltas safely on the way out.

Each worker:
    - Loops through its scenarios (from branch state).
    - Calls ``execute_scenario`` for each.
    - On fail: ``analyze_failure`` -> (optional ``simulate_fix``) ->
      ``apply_fix`` -> retry (up to ``MAX_HEAL_RETRIES``).
    - Returns accumulated results + events as a state delta.
"""

from __future__ import annotations

from typing import Any

from .domain_classifier import Domain
from .graph import MAX_HEAL_RETRIES
from .nodes.analyze_failure import analyze_failure
from .nodes.apply_fix import apply_fix
from .nodes.execute_scenario import execute_scenario
from .nodes.simulate_fix import simulate_fix
from .state import AgentState, make_event


def _merge(state: AgentState, update: dict, events: list, results: list) -> AgentState:
    """Apply a node's partial update onto the worker's local state.

    The orchestrator state is shared at run-end via the parent reducers,
    but during the worker's heal loop we keep a local mutable state so
    subsequent node calls see the latest scenario_index, diagnosis, etc.
    """
    new_state = dict(state)
    for k, v in update.items():
        if k == "events" and isinstance(v, list):
            events.extend(v)
        elif k == "results" and isinstance(v, list):
            results.extend(v)
            new_state["results"] = results  # local cumulative for analyzer
        else:
            new_state[k] = v
    return new_state  # type: ignore[return-value]


async def _run_one_scenario(
    state: AgentState, *, twin_enabled: bool,
    defer_heals: bool,
    events: list, results: list,
    pending_fixes: list,
) -> AgentState:
    """Execute current scenario; if it fails, run heal loop until pass /
    veto / max-retry. Returns the post-scenario state.

    When ``defer_heals`` is True (Phase 4-J parallel + HITL mode), the
    worker stops AFTER ``analyze_failure`` -- the diagnosis goes onto
    ``pending_fixes`` instead of being applied inline. The parent
    graph drains the queue serially with operator approval.
    """
    # Initial execution
    update = await execute_scenario(state)
    state = _merge(state, update, events, results)

    while True:
        last = (state.get("results") or [])[-1] if state.get("results") else {}
        status = last.get("status", "pass")
        retries = state.get("heal_retry_count", 0)

        if status != "fail" or retries >= MAX_HEAL_RETRIES:
            return state

        # Failure with retries left -> diagnose
        update = await analyze_failure(state)
        state = _merge(state, update, events, results)

        diag = state.get("diagnosis") or {}
        action = diag.get("corrective_action_type", "")
        if action != "xcp_calibration":
            return state  # escalate -- nothing to apply

        if defer_heals:
            # Phase 4-J: hand the fix off to the serial replay loop.
            # The worker stops here -- it does NOT apply, does NOT
            # re-execute. Parent's ``next_pending_fix`` /
            # ``apply_pending_one`` nodes handle commit + retry.
            pending_fixes.append({
                "scenario": dict(state.get("current_scenario") or {}),
                "diagnosis": dict(diag),
                "domain": state.get("current_domain", "general"),
            })
            return state

        # Optional twin gate
        if twin_enabled:
            update = await simulate_fix(state)
            state = _merge(state, update, events, results)
            pred = state.get("twin_prediction") or {}
            if pred.get("verdict") == "veto":
                return state  # twin blocked the fix

        # Commit + re-execute
        update = await apply_fix(state)
        state = _merge(state, update, events, results)

        update = await execute_scenario(state)
        state = _merge(state, update, events, results)


async def _run_domain_worker(
    state: AgentState, domain: Domain,
) -> dict[str, Any]:
    """Process every scenario in this branch state's slice.

    Branch state carries:
        - ``scenarios``: only this domain's scenarios
        - ``scenario_index``: starts at 0
        - ``current_domain``: pre-set to ``domain``
    Other fields (model_signals, dut_backend, twin_enabled, ...) are
    inherited from the parent state via Send.

    When ``state["hitl_active"]`` is set (Phase 4-J), workers operate
    in **deferred-heal mode**: they diagnose failures but DON'T apply
    fixes. Each diagnosis lands in ``pending_fixes`` (operator.add
    reducer merges across siblings) for the parent's serial heal
    replay loop to pick up.
    """
    scenarios = state.get("scenarios", [])
    twin_enabled = bool(state.get("twin_enabled"))
    defer_heals = bool(state.get("hitl_active"))
    label = f"{domain}_agent"

    mode_tag = " [defer-heals]" if defer_heals else ""
    events: list = [make_event(
        label, "thought",
        f"[{label}] starting parallel run on {len(scenarios)} scenario(s){mode_tag}",
        {"domain": domain, "scenario_count": len(scenarios),
         "defer_heals": defer_heals},
    )]
    results: list = []
    pending_fixes: list = []

    # Local mutable state -- branch is isolated from siblings.
    local: AgentState = dict(state)  # type: ignore[assignment]
    local["scenarios"] = scenarios
    local["scenario_index"] = 0
    local["heal_retry_count"] = 0
    local["current_domain"] = domain

    for idx in range(len(scenarios)):
        local["scenario_index"] = idx
        local["heal_retry_count"] = 0
        local["current_scenario"] = None
        local["diagnosis"] = None
        local = await _run_one_scenario(
            local, twin_enabled=twin_enabled,
            defer_heals=defer_heals,
            events=events, results=results,
            pending_fixes=pending_fixes,
        )

    events.append(make_event(
        label, "result",
        f"[{label}] done -- "
        f"pass={sum(1 for r in results if r.get('status') == 'pass')} "
        f"fail={sum(1 for r in results if r.get('status') == 'fail')} "
        f"err={sum(1 for r in results if r.get('status') == 'error')}"
        + (f" deferred={len(pending_fixes)}" if pending_fixes else ""),
    ))

    # Return only the deltas the parent reducer should merge.
    return {
        "events": events,
        "results": results,
        "pending_fixes": pending_fixes,
    }


# ---------------------------------------------------------------------------
# Per-domain worker entry points (LangGraph nodes target one of these)
# ---------------------------------------------------------------------------

async def bms_worker(state: AgentState) -> dict[str, Any]:
    return await _run_domain_worker(state, "bms")


async def pcs_worker(state: AgentState) -> dict[str, Any]:
    return await _run_domain_worker(state, "pcs")


async def grid_worker(state: AgentState) -> dict[str, Any]:
    return await _run_domain_worker(state, "grid")


async def general_worker(state: AgentState) -> dict[str, Any]:
    return await _run_domain_worker(state, "general")
