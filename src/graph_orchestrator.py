"""graph_orchestrator.py -- Phase 4-B multi-agent StateGraph.

Topology:

    START
      |
    load_model
      |
    plan_tests              (domain-classifies + sorts scenarios)
      |
    classify_domains        (announces dispatch, seeds current_domain)
      |
   +--+----+----+----------+
   |       |    |          |
   bms   pcs  grid     general    <-- per-domain "agent" markers (one node each)
   agent agent agent    agent
   |       |    |          |
   +--+----+----+----------+
      |
    execute_scenario
      |
   [route_after_exec]
    fail / next
      |
    analyze_failure -> [route_after_analysis] -> apply_fix -> execute (heal)
    advance_scenario
      |
   [route_after_advance]
    bms_agent | pcs_agent | grid_agent | general_agent | aggregate
      |                                                    |
      +-----<heal/loop back into matching agent>-----------+
                                                           |
                                                       aggregate
                                                           |
                                                       generate_report
                                                           |
                                                          END

Why per-agent marker nodes (instead of compiled subgraphs as nodes)?
LangGraph compiled subgraphs share ``operator.add`` channels with their
parent: emissions from inside the subgraph are added to the parent
twice (once during the subgraph run, once when the subgraph result
collapses), which doubles ``events`` and ``results``. Marker nodes
avoid that while still putting "BMS Agent / PCS Agent / Grid Agent" on
the graph topology -- visible in LangSmith and in
``draw_mermaid()``-style introspection.
"""

from __future__ import annotations

from typing import Hashable, Literal

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from .domain_classifier import ALL_DOMAINS, Domain
from .parallel_agents import (
    bms_worker, general_worker, grid_worker, pcs_worker,
)
from .nodes.advance_scenario import advance_scenario
from .nodes.analyze_failure import analyze_failure
from .nodes.apply_fix import apply_fix
from .nodes.execute_scenario import execute_scenario
from .nodes.generate_report import generate_report
from .nodes.load_model import load_model
from .nodes.plan_tests import plan_tests
from .nodes.simulate_fix import simulate_fix
from .state import AgentState, make_event

# Reuse single-agent constants + checkpointer helpers so HITL / SQLite
# semantics stay identical across the two graphs (Phase 4-D).
from .graph import (
    MAX_HEAL_RETRIES,
    _build_compile_kwargs,
    _open_async_sqlite_saver,
    _resolve_hitl_and_db,
    make_sqlite_checkpointer,
    route_after_analysis,
    route_after_simulation,
    wire_heal_edges,
)


_AGENT_NODES = {
    "bms": "bms_agent",
    "pcs": "pcs_agent",
    "grid": "grid_agent",
    "general": "general_agent",
}


# ---------------------------------------------------------------------------
# Per-agent marker node factory
# ---------------------------------------------------------------------------

def _make_agent_marker(domain: Domain):
    """Build the per-agent marker node.

    The node only emits a "agent X handling scenario Y" event and refreshes
    ``current_domain`` on the way to ``execute_scenario``. All real work
    (stimulus / capture / heal loop / advance) happens in the shared
    downstream nodes -- the per-agent identity is kept visible only in
    state + events.
    """
    label = f"{domain}_agent"

    async def agent_marker(state: AgentState) -> dict:
        scenarios = state.get("scenarios", [])
        idx = state.get("scenario_index", 0)
        scenario = scenarios[idx] if 0 <= idx < len(scenarios) else None
        sid = scenario.get("scenario_id", "?") if scenario else "?"
        return {
            "current_domain": domain,
            "events": [make_event(
                label, "thought",
                f"[{label}] handling scenario {sid}",
                {"domain": domain, "scenario_id": sid, "index": idx},
            )],
        }

    agent_marker.__name__ = f"{domain}_agent_marker"
    return agent_marker


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------

def route_after_exec_orch(
    state: AgentState,
) -> Literal["fail", "next"]:
    """After execute_scenario in the orchestrator: heal or advance."""
    results = state.get("results", [])
    if results:
        last_status = results[-1].get("status", "pass")
        retries = state.get("heal_retry_count", 0)
        if last_status == "fail" and retries < MAX_HEAL_RETRIES:
            return "fail"
    return "next"


def _scenario_domain_at(state: AgentState, idx: int) -> str:
    scenarios = state.get("scenarios", [])
    if 0 <= idx < len(scenarios):
        return scenarios[idx].get("domain", "general")
    return ""


def route_after_advance(
    state: AgentState,
) -> Literal["bms", "pcs", "grid", "general", "aggregate"]:
    """After advancing scenario_index, dispatch to the matching agent.

    When all scenarios are done, fall through to ``aggregate``.
    """
    idx = state.get("scenario_index", 0)
    d = _scenario_domain_at(state, idx)
    if d in ALL_DOMAINS:
        return d  # type: ignore[return-value]
    return "aggregate"


def _route_to_first_agent(
    state: AgentState,
) -> Literal["bms", "pcs", "grid", "general", "aggregate"]:
    """After classify_domains, jump to the first agent that has scenarios."""
    counts = state.get("domain_counts", {}) or {}
    for d in ALL_DOMAINS:
        if counts.get(d):
            return d  # type: ignore[return-value]
    return "aggregate"


# ---------------------------------------------------------------------------
# Orchestrator nodes
# ---------------------------------------------------------------------------

async def classify_domains(state: AgentState) -> dict:
    """Hand-off node: announce dispatch + seed current_domain."""
    scenarios = state.get("scenarios", [])
    counts = state.get("domain_counts", {}) or {}
    first_domain = scenarios[0].get("domain", "general") if scenarios else "general"

    summary = ", ".join(
        f"{d}={counts.get(d, 0)}" for d in ALL_DOMAINS if counts.get(d)
    )
    return {
        "current_domain": first_domain,
        "events": [make_event(
            "orchestrator", "thought",
            f"Dispatching {len(scenarios)} scenario(s) across agents: "
            + (summary or "general=0"),
            {"domain_counts": dict(counts), "first_domain": first_domain},
        )],
    }


# ---------------------------------------------------------------------------
# Phase 4-J: serial heal-replay loop for parallel + HITL
#
# Workers in defer-heals mode collect (scenario, diagnosis) tuples on
# ``pending_fixes`` instead of healing inline. After fan-out join,
# this trio of nodes drains the queue one-fix-at-a-time so the
# operator can approve / reject each commit.
#
#   next_pending_fix    advance the read cursor, seed current_scenario
#   approve_fix         pass-through marker (HITL pauses BEFORE this)
#   apply_pending_one   simulate_fix? -> apply_fix -> execute_scenario
#                       (re-test the scenario with the new calibration)
#
# The conditional edge ``route_has_pending`` loops back until the
# queue drains, then routes to ``aggregate``.
# ---------------------------------------------------------------------------

async def next_pending_fix(state: AgentState) -> dict:
    """Pop the next pending-fix entry into ``current_*`` state."""
    queue = state.get("pending_fixes") or []
    idx = state.get("pending_fix_index", 0)
    if idx >= len(queue):
        return {"events": [make_event(
            "heal_replay", "thought",
            "no more pending fixes",
        )]}
    entry = queue[idx]
    scenario = entry.get("scenario") or {}
    diagnosis = entry.get("diagnosis") or {}
    domain = entry.get("domain", "general")
    sid = scenario.get("scenario_id", "?")
    return {
        "current_scenario": dict(scenario),
        "diagnosis": dict(diagnosis),
        "current_domain": domain,
        # ``scenario_index`` must point at the matching scenario in
        # ``state["scenarios"]`` so apply_fix and execute_scenario read
        # the right spec when they re-run.
        "scenario_index": _find_scenario_index(state, sid),
        "heal_retry_count": 0,
        "pending_fix_index": idx + 1,
        "events": [make_event(
            "heal_replay", "thought",
            f"replaying fix {idx + 1}/{len(queue)} -- {sid} ({domain})",
            {"scenario_id": sid, "domain": domain,
             "param": diagnosis.get("corrective_param"),
             "value": diagnosis.get("corrective_value")},
        )],
    }


def _find_scenario_index(state: AgentState, sid: str) -> int:
    """Best-effort lookup of the scenario in ``state["scenarios"]``.

    Falls back to 0 when no match is found -- the apply_fix +
    execute_scenario nodes will still run on the seeded
    ``current_scenario`` dict.
    """
    for i, s in enumerate(state.get("scenarios", [])):
        if s.get("scenario_id") == sid:
            return i
    return 0


async def approve_fix(state: AgentState) -> dict:
    """Marker node. HITL pauses BEFORE this so the operator can vet
    the proposed fix shown in ``current_scenario`` / ``diagnosis``.

    No state change -- the interrupt + main.py prompt loop does the
    real work. After resume, control flows on to ``apply_pending_one``.
    """
    return {"events": [make_event(
        "heal_replay", "action",
        "fix approved -- proceeding to apply",
    )]}


def route_has_pending(
    state: AgentState,
) -> Literal["yes", "no"]:
    """Loop control: keep replaying while ``pending_fixes`` has entries
    we haven't drained yet."""
    queue = state.get("pending_fixes") or []
    idx = state.get("pending_fix_index", 0)
    return "yes" if idx < len(queue) else "no"


async def aggregate_results(state: AgentState) -> dict:
    """Per-domain summary right before the report node."""
    results = state.get("results", [])
    scenarios = state.get("scenarios", [])
    by_id = {s.get("scenario_id"): s for s in scenarios}

    per_domain: dict[str, dict[str, int]] = {}
    for r in results:
        sid = r.get("scenario_id")
        d = (by_id.get(sid) or {}).get("domain", "general")
        bucket = per_domain.setdefault(
            d, {"pass": 0, "fail": 0, "error": 0, "skipped": 0},
        )
        status = r.get("status", "pass")
        bucket[status] = bucket.get(status, 0) + 1

    parts = []
    for d in ALL_DOMAINS:
        if d in per_domain:
            b = per_domain[d]
            parts.append(
                f"{d}_agent: pass={b.get('pass', 0)} fail={b.get('fail', 0)} "
                f"err={b.get('error', 0)}"
            )
    summary = " | ".join(parts) if parts else "no results"

    return {
        "events": [make_event(
            "orchestrator", "result",
            f"Multi-agent summary -> {summary}",
            {"per_domain": per_domain},
        )],
    }


# ---------------------------------------------------------------------------
# Orchestrator graph
# ---------------------------------------------------------------------------

def build_orchestrator_graph(*, twin: bool = False) -> StateGraph:
    """Top-level multi-agent StateGraph. Compile to run, like build_graph().

    When ``twin=True`` (Phase 4-C), inserts a ``simulate_fix`` node
    between ``analyze_failure`` and ``apply_fix`` -- same opt-in pattern
    as :func:`src.graph.build_graph`.
    """
    g = StateGraph(AgentState)

    # Pipeline nodes
    g.add_node("load_model", load_model)
    g.add_node("plan_tests", plan_tests)
    g.add_node("classify_domains", classify_domains)

    # Per-agent marker nodes
    for d in ALL_DOMAINS:
        g.add_node(_AGENT_NODES[d], _make_agent_marker(d))

    # Shared work nodes (single instances; per-agent context lives in state)
    g.add_node("execute_scenario", execute_scenario)
    g.add_node("analyze_failure", analyze_failure)
    g.add_node("apply_fix", apply_fix)
    g.add_node("advance_scenario", advance_scenario)
    g.add_node("aggregate", aggregate_results)
    g.add_node("generate_report", generate_report)
    if twin:
        g.add_node("simulate_fix", simulate_fix)

    g.set_entry_point("load_model")
    g.add_edge("load_model", "plan_tests")
    g.add_edge("plan_tests", "classify_domains")

    # classify_domains -> first non-empty agent (or aggregate when no work)
    dispatcher_map: dict[Hashable, str] = {
        "bms": _AGENT_NODES["bms"],
        "pcs": _AGENT_NODES["pcs"],
        "grid": _AGENT_NODES["grid"],
        "general": _AGENT_NODES["general"],
        "aggregate": "aggregate",
    }
    g.add_conditional_edges(
        "classify_domains", _route_to_first_agent, dispatcher_map,
    )

    # Each agent marker -> execute_scenario
    for d in ALL_DOMAINS:
        g.add_edge(_AGENT_NODES[d], "execute_scenario")

    # execute_scenario -> heal-or-advance
    g.add_conditional_edges(
        "execute_scenario", route_after_exec_orch,
        {"fail": "analyze_failure", "next": "advance_scenario"},
    )

    # analyze_failure -> retry / escalate. Shared with the single-agent
    # graph via :func:`wire_heal_edges`.
    wire_heal_edges(g, twin=twin)

    # apply_fix -> execute_scenario (heal loop, scenario_index unchanged)
    g.add_edge("apply_fix", "execute_scenario")

    # advance_scenario -> dispatch to matching agent for the new scenario,
    # or aggregate when there are no more scenarios.
    g.add_conditional_edges(
        "advance_scenario", route_after_advance, dispatcher_map,
    )

    g.add_edge("aggregate", "generate_report")
    g.add_edge("generate_report", END)

    return g


_PARALLEL_WORKERS = {
    "bms": ("bms_worker", bms_worker),
    "pcs": ("pcs_worker", pcs_worker),
    "grid": ("grid_worker", grid_worker),
    "general": ("general_worker", general_worker),
}


def fan_out_parallel(state: AgentState) -> list[Send]:
    """Conditional-edge function: send each non-empty domain in parallel.

    Each Send carries a branch state containing only the scenarios for
    that domain. The four ``*_worker`` nodes run concurrently; their
    deltas merge into the parent via ``operator.add`` reducers on
    ``events`` and ``results``.

    Uses :func:`copy.deepcopy` for nested-dict fields (``dut_config``,
    ``device_pool``, ``rag_context_by_domain``) so a worker that
    mutates them in-place can never race a sibling.
    """
    from copy import deepcopy

    scenarios = state.get("scenarios", [])
    # Fields that may be nested mutable dicts/lists. We deepcopy these
    # once per fan-out and share the *copies* across branches; each
    # branch owns an independent reference, so concurrent writes
    # cannot stomp on the parent or on siblings.
    nested_keys = (
        "dut_config", "device_pool",
        "rag_context_by_domain", "domain_counts",
    )
    sends: list[Send] = []
    for d in ALL_DOMAINS:
        subset = [s for s in scenarios if s.get("domain") == d]
        if not subset:
            continue
        worker_node = _PARALLEL_WORKERS[d][0]
        # Shallow-copy the parent state then deepcopy the nested
        # mutables. Scenario dicts in ``subset`` are also deepcopied
        # so a worker that adds ``validation_errors`` doesn't poison
        # the parent's ``state["scenarios"]``.
        branch_state: dict = {**state}
        for k in nested_keys:
            if k in branch_state and branch_state[k] is not None:
                branch_state[k] = deepcopy(branch_state[k])
        branch_state.update({
            "scenarios": [deepcopy(s) for s in subset],
            "scenario_index": 0,
            "heal_retry_count": 0,
            "current_scenario": None,
            "diagnosis": None,
            "current_domain": d,
            # Reset reducer-tracked lists in the branch so the worker's
            # "events" / "results" / "pending_fixes" delta does not
            # redundantly include the parent's earlier accumulator.
            "events": [],
            "results": [],
            "pending_fixes": [],
        })
        sends.append(Send(worker_node, branch_state))
    return sends


def build_parallel_orchestrator_graph(
    *, twin: bool = False, hitl: bool = False,
) -> StateGraph:
    """Phase 4-F parallel orchestrator (Phase 4-J adds HITL replay).

    Each non-empty domain runs in parallel via :class:`Send`. Hardware
    races on the shared HIL device are prevented by per-device
    ``asyncio.Lock`` inside the DUT backends; per-agent state is
    isolated by Send branch state.

    When ``hitl=True``, workers run in **defer-heals mode**: they
    diagnose failures via Claude in parallel but do NOT apply fixes
    inline. The diagnosed (scenario, diagnosis) pairs land on
    ``pending_fixes`` (operator.add reducer merges across siblings).
    After fan-out join, the graph drains the queue serially:

        next_pending_fix -> [interrupt before approve_fix]
                         -> approve_fix -> apply_fix -> execute_scenario
                         -> route_has_pending (loop)

    Operators see one fix at a time. The compile-time ``twin`` flag
    still controls whether ``simulate_fix`` gates ``apply_fix`` as in
    the serial graph.
    """
    g = StateGraph(AgentState)

    g.add_node("load_model", load_model)
    g.add_node("plan_tests", plan_tests)
    g.add_node("classify_domains", classify_domains)
    for d in ALL_DOMAINS:
        node_name, fn = _PARALLEL_WORKERS[d]
        g.add_node(node_name, fn)
    g.add_node("aggregate", aggregate_results)
    g.add_node("generate_report", generate_report)

    g.set_entry_point("load_model")
    g.add_edge("load_model", "plan_tests")
    g.add_edge("plan_tests", "classify_domains")

    # Conditional fan-out: returns a list of Sends, one per non-empty
    # domain. Targets must include every possible worker node + the
    # fall-through to aggregate (when no scenarios at all).
    g.add_conditional_edges(
        "classify_domains",
        fan_out_parallel,
        {
            **{d: _PARALLEL_WORKERS[d][0] for d in ALL_DOMAINS},
            "aggregate": "aggregate",
        },
    )

    if hitl:
        # Workers join into ``next_pending_fix`` rather than
        # ``aggregate`` directly; the replay loop drains the queue
        # before the report runs.
        g.add_node("next_pending_fix", next_pending_fix)
        g.add_node("approve_fix", approve_fix)
        g.add_node("apply_fix", apply_fix)
        g.add_node("execute_scenario", execute_scenario)
        if twin:
            g.add_node("simulate_fix", simulate_fix)

        for d in ALL_DOMAINS:
            g.add_edge(_PARALLEL_WORKERS[d][0], "next_pending_fix")

        # First-pass routing: when no fixes were deferred, skip
        # straight to aggregate. Otherwise enter the HITL replay.
        g.add_conditional_edges(
            "next_pending_fix",
            route_has_pending,
            {"yes": "approve_fix", "no": "aggregate"},
        )
        # approve_fix -> (twin?) simulate_fix -> apply_fix -> execute -> loop
        if twin:
            g.add_edge("approve_fix", "simulate_fix")
            g.add_conditional_edges(
                "simulate_fix",
                route_after_simulation,
                {"commit": "apply_fix", "veto": "next_pending_fix"},
            )
        else:
            g.add_edge("approve_fix", "apply_fix")
        g.add_edge("apply_fix", "execute_scenario")
        g.add_edge("execute_scenario", "next_pending_fix")
    else:
        # Each worker -> aggregate. LangGraph waits for ALL active Sends
        # before progressing past the join.
        for d in ALL_DOMAINS:
            g.add_edge(_PARALLEL_WORKERS[d][0], "aggregate")

    g.add_edge("aggregate", "generate_report")
    g.add_edge("generate_report", END)

    return g


def compile_parallel_orchestrator_graph(
    *,
    twin: bool = False,
    hitl: bool = False,
    interrupt_nodes: tuple[str, ...] = ("approve_fix",),
    checkpointer=None,
    checkpoint_db: str | None = None,
):
    """Build and compile the parallel orchestrator.

    Phase 4-J: ``hitl=True`` enables the deferred-heal replay loop.
    Workers diagnose in parallel (the win), then the parent applies
    fixes one-at-a-time with operator approval pausing **before** the
    ``approve_fix`` marker. SQLite checkpointing works the same way
    as the serial orchestrator -- pass ``checkpoint_db=<path>`` for
    persistent resume.
    """
    hitl_resolved, db_resolved = _resolve_hitl_and_db(hitl, checkpoint_db)
    if checkpointer is None and db_resolved:
        checkpointer = make_sqlite_checkpointer(db_resolved)
    g = build_parallel_orchestrator_graph(twin=twin, hitl=hitl_resolved)
    return g.compile(
        **_build_compile_kwargs(hitl_resolved, interrupt_nodes, checkpointer),
    )


async def acompile_parallel_orchestrator_graph(
    *,
    twin: bool = False,
    hitl: bool = False,
    interrupt_nodes: tuple[str, ...] = ("approve_fix",),
    checkpointer=None,
    checkpoint_db: str | None = None,
):
    """Async-aware compile for the parallel orchestrator (Phase 4-J).

    Use this whenever ``checkpoint_db`` is set so the
    ``AsyncSqliteSaver`` opens its connection inside the caller's
    running event loop.
    """
    hitl_resolved, db_resolved = _resolve_hitl_and_db(hitl, checkpoint_db)
    if checkpointer is None and db_resolved:
        checkpointer = await _open_async_sqlite_saver(db_resolved)
    g = build_parallel_orchestrator_graph(twin=twin, hitl=hitl_resolved)
    return g.compile(
        **_build_compile_kwargs(hitl_resolved, interrupt_nodes, checkpointer),
    )


def compile_orchestrator_graph(
    *,
    hitl: bool | None = None,
    interrupt_nodes: tuple[str, ...] = ("apply_fix",),
    checkpointer=None,
    checkpoint_db: str | None = None,
    twin: bool = False,
):
    """Build and compile the orchestrator graph.

    Mirrors :func:`src.graph.compile_graph` -- supports HITL pauses and
    sync SQLite-backed checkpointing (Phase 4-D). For async SQLite use
    :func:`acompile_orchestrator_graph`.
    """
    hitl, checkpoint_db = _resolve_hitl_and_db(hitl, checkpoint_db)
    if checkpointer is None and checkpoint_db:
        checkpointer = make_sqlite_checkpointer(checkpoint_db)

    g = build_orchestrator_graph(twin=twin)
    return g.compile(**_build_compile_kwargs(hitl, interrupt_nodes, checkpointer))


async def acompile_orchestrator_graph(
    *,
    hitl: bool | None = None,
    interrupt_nodes: tuple[str, ...] = ("apply_fix",),
    checkpointer=None,
    checkpoint_db: str | None = None,
    twin: bool = False,
):
    """Async-aware compile for the orchestrator graph.

    Use this whenever ``checkpoint_db`` is set so the
    ``AsyncSqliteSaver`` opens its ``aiosqlite`` connection inside the
    caller's running event loop. Mirrors :func:`src.graph.acompile_graph`.

    The caller is responsible for closing the checkpointer connection
    when the run is done -- ``main.run_cli`` already does this in its
    ``finally`` block.
    """
    hitl, checkpoint_db = _resolve_hitl_and_db(hitl, checkpoint_db)
    if checkpointer is None and checkpoint_db:
        checkpointer = await _open_async_sqlite_saver(checkpoint_db)

    g = build_orchestrator_graph(twin=twin)
    return g.compile(**_build_compile_kwargs(hitl, interrupt_nodes, checkpointer))
