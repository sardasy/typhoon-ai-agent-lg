"""
LangGraph State — the single TypedDict that flows through every node.

Design principle: Each node reads what it needs, writes what it produces.
LangGraph merges partial returns into the running state automatically.

Reducer annotations (Annotated[..., operator.add]) tell LangGraph to
*append* rather than replace when a node returns a list value.
"""

from __future__ import annotations

import operator
import time
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic models for structured data inside state
# ---------------------------------------------------------------------------

class ScenarioSpec(BaseModel):
    """Single test scenario (output of planner)."""
    scenario_id: str
    name: str
    description: str = ""
    category: str = "protection"
    priority: int = 1
    standard_ref: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    measurements: list[str] = Field(default_factory=list)
    pass_fail_rules: dict[str, Any] = Field(default_factory=dict)
    depends_on: str | None = None


class WaveformStats(BaseModel):
    signal: str
    mean: float = 0
    max: float = 0
    min: float = 0
    rms: float = 0
    overshoot_percent: float | None = None
    rise_time_ms: float | None = None
    settling_time_ms: float | None = None


class ScenarioResult(BaseModel):
    """Result of executing one scenario."""
    scenario_id: str
    status: Literal["pass", "fail", "error", "skipped"] = "pass"
    duration_s: float = 0
    waveform_stats: list[WaveformStats] = Field(default_factory=list)
    fail_reason: str = ""
    retry_count: int = 0
    root_cause: str = ""
    corrective_action: str = ""
    timestamp: float = Field(default_factory=time.time)


class DiagnosisResult(BaseModel):
    """Output of the analyzer node."""
    failed_scenario_id: str
    root_cause_category: str = ""         # firmware | hardware | tuning | wiring
    root_cause_description: str = ""
    confidence: float = 0.5
    corrective_action_type: str = ""      # xcp_calibration | retest | escalate
    corrective_param: str = ""
    corrective_value: float | None = None
    evidence: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """
    The full state passed between all LangGraph nodes.

    Fields with `Annotated[list, operator.add]` are APPEND-only:
    when a node returns {"results": [new_item]}, LangGraph appends
    it to the existing list instead of replacing.
    """

    # --- Input ---
    goal: str                                        # user's NL test goal
    config_path: str                                 # path to model.yaml

    # --- Model info (set by load_model) ---
    model_path: str
    model_signals: list[str]
    model_loaded: bool

    # --- RAG context (set by load_model) ---
    rag_context: str

    # --- Plan (set by plan_tests) ---
    plan_strategy: str
    scenarios: list[dict]                            # list of ScenarioSpec dicts
    scenario_index: int                              # current scenario pointer
    estimated_duration_s: float
    standard_coverage: dict[str, list[str]]

    # --- Execution (appended by execute_scenario) ---
    results: Annotated[list[dict], operator.add]     # ScenarioResult dicts
    current_scenario: dict | None                    # scenario being executed

    # --- Analysis (set by analyze_failure) ---
    diagnosis: dict | None                           # DiagnosisResult dict
    heal_retry_count: int                            # retries for current scenario

    # --- Events log (appended by every node) ---
    events: Annotated[list[dict], operator.add]      # SSE event stream

    # --- Report ---
    report_path: str

    # --- Control ---
    error: str                                       # non-empty = abort


def make_event(
    node: str,
    event_type: str,
    message: str,
    data: dict | None = None,
) -> dict:
    """Helper to create an event dict for the events log."""
    return {
        "node": node,
        "event_type": event_type,
        "message": message,
        "data": data or {},
        "timestamp": time.time(),
    }
