"""
graph.py — LangGraph StateGraph definition.

This is the heart of the system. It defines:
  - Nodes (functions that transform state)
  - Edges (unconditional transitions)
  - Conditional edges (routing logic based on state)

The compiled graph is a runnable that takes initial state and
streams events as it traverses nodes.

Graph topology:

  START
    |
  load_model
    |
  plan_tests
    |
  execute_scenario  <----+----+
    |                    |    |
  [route_after_exec]     |    |
    |    |    |          |    |
    |    |    +-- "next" -> advance_scenario
    |    |                    |
    |    |              [route_has_more]
    |    |                |       |
    |    |           "yes" -+  "no" -> generate_report -> END
    |    |
    |    +-- "fail" -> analyze_failure
    |                       |
    |                 [route_after_analysis]
    |                    |        |
    |               "retry" -> apply_fix --+
    |                                      |
    |               "escalate" -> advance_scenario
    |
    +-- "done" -> generate_report -> END
"""

from __future__ import annotations

import os
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .constants import (
    ACTION_XCP_CALIBRATION,
    ANALYZER_RETRY_MIN_CONFIDENCE,
    MAX_HEAL_RETRIES as _MAX_HEAL_RETRIES,
)
from .state import AgentState

# Import node functions
from .nodes.load_model import load_model
from .nodes.plan_tests import plan_tests
from .nodes.execute_scenario import execute_scenario
from .nodes.analyze_failure import analyze_failure
from .nodes.apply_fix import apply_fix
from .nodes.advance_scenario import advance_scenario
from .nodes.generate_report import generate_report
from .nodes.simulate_fix import simulate_fix


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------

# Re-export from src.constants so existing ``from src.graph import
# MAX_HEAL_RETRIES`` callers (graph_orchestrator, parallel_agents,
# tests) keep working without churn. Single source of truth lives in
# src.constants.
MAX_HEAL_RETRIES = _MAX_HEAL_RETRIES


def route_after_exec(state: AgentState) -> Literal["fail", "next", "done"]:
    """
    After executing a scenario, decide what to do:
      - "fail"  -> analyze the failure
      - "next"  -> advance to next scenario
      - "done"  -> no more scenarios, go to report
    """
    results = state.get("results", [])
    if not results:
        return "done"

    last_result = results[-1]
    last_status = last_result.get("status", "pass")

    # If fail and we haven't exhausted retries yet, go to analysis
    if last_status == "fail" and state.get("heal_retry_count", 0) < MAX_HEAL_RETRIES:
        return "fail"

    # Check if there are more scenarios
    idx = state.get("scenario_index", 0)
    total = len(state.get("scenarios", []))

    if idx + 1 < total:
        return "next"
    else:
        return "done"


def route_after_analysis(
    state: AgentState,
) -> Literal["retry", "escalate"]:
    """
    After analyzing a failure, decide:
      - "retry"    -> apply fix and re-execute
      - "escalate" -> give up on this scenario, move on
    """
    diagnosis = state.get("diagnosis") or {}
    action_type = diagnosis.get("corrective_action_type", "escalate")
    confidence = diagnosis.get("confidence", 0)
    retries = state.get("heal_retry_count", 0)

    # Retry if: fixable action + confidence over threshold + retries left.
    # Thresholds live in src.constants.
    if (
        action_type == ACTION_XCP_CALIBRATION
        and confidence >= ANALYZER_RETRY_MIN_CONFIDENCE
        and retries < MAX_HEAL_RETRIES
    ):
        return "retry"

    return "escalate"


def route_has_more(state: AgentState) -> Literal["yes", "no"]:
    """After advancing, check if more scenarios remain."""
    idx = state.get("scenario_index", 0)
    total = len(state.get("scenarios", []))
    return "yes" if idx < total else "no"


def route_after_simulation(state: AgentState) -> Literal["commit", "veto"]:
    """After simulate_fix (Phase 4-C): does the twin allow apply_fix?

    Verdicts ``commit`` and ``uncertain`` both proceed to apply_fix. Only
    ``veto`` skips the write and escalates this scenario.
    """
    pred = state.get("twin_prediction") or {}
    return "veto" if pred.get("verdict") == "veto" else "commit"


# ---------------------------------------------------------------------------
# Heal-loop wiring (shared between build_graph and build_orchestrator_graph)
# ---------------------------------------------------------------------------

def wire_heal_edges(
    graph: StateGraph,
    *,
    twin: bool,
    on_escalate: str = "advance_scenario",
    on_commit: str = "apply_fix",
) -> None:
    """Add the analyze -> (simulate_fix?) -> apply_fix / escalate edges.

    Centralises the ``if twin:`` branching that used to live in
    ``build_graph`` and ``build_orchestrator_graph`` (separately, with
    the same body). Caller still owns ``add_node("analyze_failure",...)``,
    ``add_node("apply_fix",...)``, ``add_node("advance_scenario",...)``
    and (when ``twin``) ``add_node("simulate_fix", simulate_fix)`` --
    only the conditional-edge wiring is shared here.

    Parameters
    ----------
    twin
        When True, wire ``analyze_failure -> simulate_fix ->
        {commit: apply_fix, veto: on_escalate}``. When False, wire
        ``analyze_failure -> {retry: apply_fix, escalate: on_escalate}``.
    on_escalate
        Node to route to when the analyzer says escalate (or the twin
        vetoes). Both single-graph and orchestrator use ``advance_scenario``.
    on_commit
        Node to route to when the fix is approved. Both graphs use
        ``apply_fix`` -- exposed as a kwarg for symmetry.
    """
    if twin:
        graph.add_conditional_edges(
            "analyze_failure",
            route_after_analysis,
            {"retry": "simulate_fix", "escalate": on_escalate},
        )
        graph.add_conditional_edges(
            "simulate_fix",
            route_after_simulation,
            {"commit": on_commit, "veto": on_escalate},
        )
    else:
        graph.add_conditional_edges(
            "analyze_failure",
            route_after_analysis,
            {"retry": on_commit, "escalate": on_escalate},
        )


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_graph(*, twin: bool = False) -> StateGraph:
    """Construct and return the LangGraph StateGraph (uncompiled).

    When ``twin=True`` (Phase 4-C), inserts ``simulate_fix`` between
    ``analyze_failure`` and ``apply_fix``. The twin can veto a fix
    (no-op / out-of-range / wrong-direction), in which case the graph
    routes to ``advance_scenario`` instead of writing to the ECU.
    """

    graph = StateGraph(AgentState)

    # --- Add nodes ---
    graph.add_node("load_model", load_model)
    graph.add_node("plan_tests", plan_tests)
    graph.add_node("execute_scenario", execute_scenario)
    graph.add_node("analyze_failure", analyze_failure)
    graph.add_node("apply_fix", apply_fix)
    graph.add_node("advance_scenario", advance_scenario)
    graph.add_node("generate_report", generate_report)
    if twin:
        graph.add_node("simulate_fix", simulate_fix)

    # --- Set entry point ---
    graph.set_entry_point("load_model")

    # --- Unconditional edges ---
    graph.add_edge("load_model", "plan_tests")
    graph.add_edge("plan_tests", "execute_scenario")
    graph.add_edge("apply_fix", "execute_scenario")       # retry loop

    # --- Conditional edges ---

    # After execution: fail / next / done
    graph.add_conditional_edges(
        "execute_scenario",
        route_after_exec,
        {
            "fail": "analyze_failure",
            "next": "advance_scenario",
            "done": "generate_report",
        },
    )

    # After analysis: retry / escalate (with optional twin gate).
    # Shared wiring lives in :func:`wire_heal_edges` so the orchestrator
    # graph uses the same logic.
    wire_heal_edges(graph, twin=twin)

    # After advancing: more scenarios? or report
    graph.add_conditional_edges(
        "advance_scenario",
        route_has_more,
        {
            "yes": "execute_scenario",
            "no": "generate_report",
        },
    )

    # Report is terminal
    graph.add_edge("generate_report", END)

    return graph


def _ensure_sqlite_schema(conn) -> None:
    """Create LangGraph checkpoint tables on a sync sqlite3 connection.

    Mirrors ``SqliteSaver.setup()`` but takes an already-opened connection
    so the caller can close it immediately. Idempotent.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver
    SqliteSaver(conn).setup()


def make_sqlite_checkpointer(db_path: str):
    """Return a *sync* ``SqliteSaver`` bound to ``db_path``.

    Used by synchronous tooling such as ``--list-threads`` (read-only
    SELECT) and by tests that exercise schema setup. For driving a
    running graph use :func:`acompile_graph` instead, which opens an
    async ``aiosqlite`` connection.
    """
    from pathlib import Path
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    p = Path(db_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


async def _open_async_sqlite_saver(db_path: str):
    """Await an ``aiosqlite.Connection`` and wrap it in ``AsyncSqliteSaver``.

    Creates the schema synchronously first (fast, idempotent), then opens
    the async connection inside the caller's event loop.
    """
    from pathlib import Path
    import sqlite3
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    p = Path(db_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    # One-time sync schema setup.
    boot = sqlite3.connect(str(p))
    try:
        _ensure_sqlite_schema(boot)
    finally:
        boot.close()
    conn = await aiosqlite.connect(str(p))
    return AsyncSqliteSaver(conn)


def _resolve_hitl_and_db(hitl, checkpoint_db):
    if hitl is None:
        hitl = os.environ.get("THAA_HITL", "").lower() in ("1", "true", "yes")
    if checkpoint_db is None:
        checkpoint_db = os.environ.get("THAA_CHECKPOINT_DB") or None
    return hitl, checkpoint_db


def _build_compile_kwargs(hitl, interrupt_nodes, checkpointer):
    compile_kwargs: dict = {}
    if hitl:
        compile_kwargs["checkpointer"] = checkpointer or MemorySaver()
        compile_kwargs["interrupt_before"] = list(interrupt_nodes)
    elif checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    return compile_kwargs


def compile_graph(
    *,
    hitl: bool | None = None,
    interrupt_nodes: tuple[str, ...] = ("apply_fix",),
    checkpointer=None,
    checkpoint_db: str | None = None,
    twin: bool = False,
):
    """Build and compile the graph synchronously.

    Parameters
    ----------
    hitl
        Human-in-the-loop mode. When True the graph pauses BEFORE
        ``interrupt_nodes`` so an operator can review the proposed action
        (typically the XCP calibration write in ``apply_fix``) and resume
        with ``Command(resume={...})`` or ``invoke(None, config)``.
        When None (default), reads ``THAA_HITL`` env var.
    interrupt_nodes
        Node names to pause before. Default: ``("apply_fix",)``.
    checkpointer
        Optional LangGraph checkpointer. When provided, takes precedence
        over ``checkpoint_db``.
    checkpoint_db
        Path to a SQLite file used by ``SqliteSaver`` for persistent
        checkpointing. This sync entry uses the sync ``SqliteSaver``,
        which LangGraph treats as a legacy-only option -- for running
        the async graph (``astream``) you almost always want
        :func:`acompile_graph` instead. When ``None``, reads
        ``THAA_CHECKPOINT_DB``; if still unset and ``hitl`` is True,
        falls back to ``MemorySaver`` (in-process only).

    LangSmith tracing is enabled automatically when these env vars are set:
      LANGCHAIN_TRACING_V2=true
      LANGCHAIN_API_KEY=ls__...
      LANGCHAIN_PROJECT=thaa          (optional, defaults to "default")
    """
    hitl, checkpoint_db = _resolve_hitl_and_db(hitl, checkpoint_db)

    if checkpointer is None and checkpoint_db:
        checkpointer = make_sqlite_checkpointer(checkpoint_db)

    graph = build_graph(twin=twin)
    return graph.compile(**_build_compile_kwargs(hitl, interrupt_nodes, checkpointer))


async def acompile_graph(
    *,
    hitl: bool | None = None,
    interrupt_nodes: tuple[str, ...] = ("apply_fix",),
    checkpointer=None,
    checkpoint_db: str | None = None,
    twin: bool = False,
):
    """Build and compile the graph, awaiting the async SQLite connection.

    Call from inside ``asyncio.run`` (or an active event loop). When
    ``checkpoint_db`` is set, attaches an ``AsyncSqliteSaver`` backed by
    an ``aiosqlite.Connection`` that works across restarts.

    The caller is responsible for closing the async connection when the
    process is done:

        saver = getattr(app, "checkpointer", None)
        if saver is not None and hasattr(saver, "conn"):
            await saver.conn.close()
    """
    hitl, checkpoint_db = _resolve_hitl_and_db(hitl, checkpoint_db)

    if checkpointer is None and checkpoint_db:
        checkpointer = await _open_async_sqlite_saver(checkpoint_db)

    graph = build_graph(twin=twin)
    return graph.compile(**_build_compile_kwargs(hitl, interrupt_nodes, checkpointer))
