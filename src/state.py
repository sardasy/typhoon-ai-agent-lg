"""
LangGraph State -- the single TypedDict that flows through every node.

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
    # Phase 4-B: which domain agent owns this scenario.
    # Set automatically by ``plan_tests`` via ``domain_classifier.annotate``.
    # Values: "bms" | "pcs" | "grid" | "general"
    domain: str = "general"
    # Phase 4-I: which physical device runs this scenario. Resolved
    # against ``state.device_pool`` at execute time. ``"default"``
    # keeps single-device behavior unchanged.
    device_id: str = "default"


class WaveformStats(BaseModel):
    signal: str
    mean: float = 0
    max: float = 0
    min: float = 0
    rms: float = 0
    overshoot_percent: float | None = None
    rise_time_ms: float | None = None
    settling_time_ms: float | None = None
    # Additional metrics computed on demand (see src/tools/hil_tools.py::_capture)
    thd_percent: float | None = None
    rocof_hz_per_s: float | None = None


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
# HTAF Code Generation models
# ---------------------------------------------------------------------------

class ParsedSignal(BaseModel):
    """A signal extracted from a .tse model file."""
    name: str
    signal_type: str = "analog"           # analog | digital
    source_type: str = ""                 # voltage_measurement | current_measurement | probe | scada
    direction: str = "output"             # input | output


class ParsedTSE(BaseModel):
    """Result of parsing a .tse Typhoon HIL model file."""
    model_name: str = ""
    topology: str = "unknown"             # boost | buck | inverter | dab | flyback | unknown
    fmt: str = "dsl"                      # dsl | xml
    analog_signals: list[str] = Field(default_factory=list)
    digital_signals: list[str] = Field(default_factory=list)
    sources: dict[str, float] = Field(default_factory=dict)
    scada_inputs: dict[str, float] = Field(default_factory=dict)
    sim_time_step: float = 1e-6
    dsp_timer_periods: float = 100e-6
    control_params: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)


class VerificationRequirement(BaseModel):
    """A single test requirement derived from topology analysis."""
    req_id: str
    name: str
    metric: str                           # output_voltage | ripple | settling_time | rms_voltage | phase_shift
    target_value: float = 0
    tolerance_fraction: float = 0.05
    duration_s: float = 0.5
    sampling_rate_hz: int = 50000
    topology_specific: bool = False


class CodegenValidationResult(BaseModel):
    """Result of validating generated test code."""
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


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
    device_mode: str                                 # "typhoon" | "vhil_mock"; set by load_model, read by generate_report
    active_preset: str                               # preset name merged by load_model ("" if none)

    # --- RAG context (set by load_model) ---
    rag_context: str
    # rag_context_by_domain: per-domain RAG snippets (Phase 4-G).
    # Keys: "bms" | "pcs" | "grid" | "general". Populated by
    # ``load_model`` via four namespace-filtered ``rag_query`` calls.
    # ``analyze_failure`` reads the entry for the failed scenario's
    # domain and falls back to ``rag_context`` (the global pull) when
    # the namespace is empty.
    rag_context_by_domain: dict

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

    # --- Multi-agent orchestration (Phase 4-B) ---
    # current_domain: which domain "agent" is processing the scenario at
    # ``scenario_index``. Set by ``classify_domains`` (or by ``plan_tests``
    # when running under the orchestrator). Read by ``analyze_failure`` to
    # overlay domain-specific guidance on the analyzer prompt, and by
    # ``execute_scenario`` to tag events.
    current_domain: str
    # domain_counts: per-domain scenario count, e.g. {"bms": 3, "grid": 5}.
    # Populated by ``plan_tests`` after classification. Read-only thereafter.
    domain_counts: dict

    # --- Phase 4-J: HITL active flag (read by parallel workers) ---
    # Parallel workers branch on this: when True they defer heals to
    # the parent's serial replay loop instead of applying inline.
    # Set by ``main.make_initial_state`` based on the ``--hitl`` /
    # ``THAA_HITL`` resolution; read by ``parallel_agents``.
    hitl_active: bool

    # --- Phase 4-J: parallel-mode HITL deferred heal queue ---
    # When the parallel orchestrator runs with --hitl, workers do NOT
    # apply fixes inline. Instead they append (scenario, diagnosis)
    # candidates here and the parent graph drains the queue serially
    # with operator approval between each commit.
    #
    # ``pending_fixes`` uses the operator.add reducer so each worker's
    # append merges into the parent state without races.
    # ``pending_fix_index`` is the read-cursor; ``next_pending_fix``
    # advances it just like ``scenario_index`` for the main loop.
    pending_fixes: Annotated[list[dict], operator.add]
    pending_fix_index: int

    # --- Digital twin (Phase 4-C) ---
    # twin_enabled: when True, the graph routes through ``simulate_fix``
    # before ``apply_fix``. The twin can veto no-op / out-of-range /
    # wrong-direction calibration writes so the agent doesn't burn a heal
    # retry on a clearly-bad change.
    twin_enabled: bool
    # twin_prediction: last :class:`src.twin.TwinPrediction` as a dict.
    # Set by ``simulate_fix``; read by the conditional edge that routes
    # to either ``apply_fix`` (commit) or ``advance_scenario`` (veto).
    twin_prediction: dict | None

    # --- Per-model safety overlay (P0 #1) ---
    # Loaded from ``configs/safety/<profile>.yaml`` when the run config's
    # ``model.safety_profile`` field is set. ``apply_fix`` builds a
    # ``Validator`` from this overlay so e.g. an ESS run can lift
    # ``max_voltage`` to 900V without forking the codebase.
    safety_config: dict

    # --- DUT abstraction (Phase 4-A) ---
    # dut_backend: which backend execute_scenario / apply_fix route through.
    #   "hil"    -- Typhoon HIL only (default, current behavior)
    #   "xcp"    -- Real ECU only via pyXCP (calibration-only, no stimulus/capture)
    #   "hybrid" -- HIL stimulus + capture, XCP calibration write
    #   "mock"   -- In-memory backend for tests
    # Read by load_model (instantiates backend) and propagated through state.
    dut_backend: str
    # dut_config: backend-specific options, e.g. {"a2l_path": "...", "xcp_uri": "..."}
    dut_config: dict
    # Phase 4-I: device_pool maps device_id -> per-device dut_config
    # overlay. ``execute_scenario`` reads ``scenario["device_id"]``,
    # picks the matching overlay, and merges it on top of ``dut_config``
    # before instantiating the backend. ``"default"`` is implicit and
    # uses ``dut_config`` as-is when this dict is empty.
    device_pool: dict

    # --- HTAF Code Generation (used ONLY by ``src/graph_codegen.py``) ---
    # These fields are kept on ``AgentState`` for backward compatibility
    # with ``main.make_initial_state`` (one factory, both pipelines).
    # New code targeting only the codegen pipeline should accept
    # :class:`CodegenState` instead -- it carries just the keys the
    # codegen nodes actually read/write, so node signatures stay
    # honest.
    tse_content: str                                 # uploaded .tse file content
    tse_path: str                                    # original file path/name
    parsed_tse: dict | None                          # ParsedTSE as dict
    test_requirements: list[dict]                    # TestRequirement dicts
    generated_files: dict[str, str]                  # relative_path -> code content
    codegen_validation: dict | None                  # CodegenValidationResult dict
    export_path: str                                 # path to exported test suite
    codegen_mode: str                                # "mock" or "typhoon"


# ---------------------------------------------------------------------------
# Codegen-only state (sibling of AgentState, sees only codegen fields)
# ---------------------------------------------------------------------------

class CodegenState(TypedDict, total=False):
    """State for the HTAF codegen pipeline (``src/graph_codegen.py``).

    A scoped slice of the verify-pipeline ``AgentState`` -- carries
    only the keys the five codegen nodes (parse_tse, map_requirements,
    generate_tests, validate_code, export_tests) actually read or
    write. Use this as the type hint for codegen nodes so signatures
    don't lie about what they touch.

    ``total=False`` because the pipeline accepts a partial dict and
    fills the rest as it runs. Runtime is just a dict, so an
    ``AgentState`` value still satisfies ``CodegenState`` -- no
    migration needed for callers that already pass ``AgentState``.
    """
    tse_content: str
    tse_path: str
    parsed_tse: dict | None
    test_requirements: list[dict]
    generated_files: dict[str, str]
    codegen_validation: dict | None
    export_path: str
    codegen_mode: str
    # Shared with AgentState: every node still appends to events.
    events: Annotated[list[dict], operator.add]
    error: str


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
