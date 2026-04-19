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

from .state import AgentState

# Import node functions
from .nodes.load_model import load_model
from .nodes.plan_tests import plan_tests
from .nodes.execute_scenario import execute_scenario
from .nodes.analyze_failure import analyze_failure
from .nodes.apply_fix import apply_fix
from .nodes.advance_scenario import advance_scenario
from .nodes.generate_report import generate_report


# ---------------------------------------------------------------------------
# Conditional edge functions
# ---------------------------------------------------------------------------

MAX_HEAL_RETRIES = 3


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

    # Retry if: fixable action + confidence > 0.5 + retries left
    if (
        action_type == "xcp_calibration"
        and confidence >= 0.5
        and retries < MAX_HEAL_RETRIES
    ):
        return "retry"

    return "escalate"


def route_has_more(state: AgentState) -> Literal["yes", "no"]:
    """After advancing, check if more scenarios remain."""
    idx = state.get("scenario_index", 0)
    total = len(state.get("scenarios", []))
    return "yes" if idx < total else "no"


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """Construct and return the compiled LangGraph StateGraph."""

    graph = StateGraph(AgentState)

    # --- Add nodes ---
    graph.add_node("load_model", load_model)
    graph.add_node("plan_tests", plan_tests)
    graph.add_node("execute_scenario", execute_scenario)
    graph.add_node("analyze_failure", analyze_failure)
    graph.add_node("apply_fix", apply_fix)
    graph.add_node("advance_scenario", advance_scenario)
    graph.add_node("generate_report", generate_report)

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

    # After analysis: retry / escalate
    graph.add_conditional_edges(
        "analyze_failure",
        route_after_analysis,
        {
            "retry": "apply_fix",
            "escalate": "advance_scenario",
        },
    )

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


def compile_graph(
    *,
    hitl: bool | None = None,
    interrupt_nodes: tuple[str, ...] = ("apply_fix",),
    checkpointer=None,
):
    """Build and compile the graph, ready to invoke.

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
        Optional LangGraph checkpointer. When ``hitl`` is True and no
        checkpointer is supplied, an in-process ``MemorySaver`` is used.

    LangSmith tracing is enabled automatically when these env vars are set:
      LANGCHAIN_TRACING_V2=true
      LANGCHAIN_API_KEY=ls__...
      LANGCHAIN_PROJECT=thaa          (optional, defaults to "default")
    """
    if hitl is None:
        hitl = os.environ.get("THAA_HITL", "").lower() in ("1", "true", "yes")

    graph = build_graph()
    compile_kwargs: dict = {}
    if hitl:
        compile_kwargs["checkpointer"] = checkpointer or MemorySaver()
        compile_kwargs["interrupt_before"] = list(interrupt_nodes)
    elif checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    return graph.compile(**compile_kwargs)
