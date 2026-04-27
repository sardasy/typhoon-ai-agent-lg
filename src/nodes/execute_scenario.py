"""
Node: execute_scenario

Picks the current scenario (by scenario_index), executes it on HIL,
captures waveforms, evaluates pass/fail, and appends the result.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..evaluator import evaluate as evaluate_rules
from ..fault_templates import get_template, validate_params
from ..heartbeat import beat as _heartbeat
from ..liveness import observe as _liveness_observe
from ..state import AgentState, ScenarioResult, WaveformStats, make_event
from .load_model import get_dut

logger = logging.getLogger(__name__)


async def execute_scenario(state: AgentState) -> dict[str, Any]:
    """Execute one scenario and append result."""

    scenarios = state.get("scenarios", [])
    idx = state.get("scenario_index", 0)

    if idx >= len(scenarios):
        return {
            "events": [make_event("execute", "error", "No more scenarios to execute")],
        }

    scenario = scenarios[idx]
    sid = scenario.get("scenario_id", f"sc_{idx}")
    name = scenario.get("name", sid)
    params = scenario.get("parameters", {})
    measurements = scenario.get("measurements", [])
    rules = scenario.get("pass_fail_rules", {})

    # Pre-check: refuse to run scenarios whose signals aren't in the model.
    # plan_tests attached `validation_errors`; short-circuit to ERROR so we
    # don't waste stimulus / capture time on a guaranteed failure.
    pre_errors = scenario.get("validation_errors") or []
    if pre_errors:
        reason = "pre-check: " + "; ".join(pre_errors[:3])
        if len(pre_errors) > 3:
            reason += f" (+{len(pre_errors) - 3} more)"
        result = ScenarioResult(
            scenario_id=sid, status="error",
            duration_s=0.0, waveform_stats=[],
            fail_reason=reason,
            retry_count=state.get("heal_retry_count", 0),
        )
        return {
            "current_scenario": scenario,
            "results": [result.model_dump()],
            "diagnosis": None,
            "events": [make_event("execute", "error",
                                   f"SKIP {sid}: {reason}",
                                   result.model_dump())],
        }

    # Phase 4-I: per-scenario device routing. Scenarios with no
    # ``device_id`` field hit the same default backend as before.
    # P0 #3: when running on the mock DUT and the scenario YAML pinned
    # ``mock_expected_status``, short-circuit to that status without
    # invoking stimulus / capture / evaluator. Saves Claude tokens on
    # smoke-test runs of large scenario libraries.
    if state.get("dut_backend") == "mock":
        forced = scenario.get("mock_expected_status")
        if forced in ("pass", "fail", "error", "skipped"):
            forced_result = ScenarioResult(
                scenario_id=sid, status=forced,
                duration_s=0.0, waveform_stats=[],
                fail_reason="" if forced == "pass" else "mock_expected_status override",
                retry_count=state.get("heal_retry_count", 0),
            )
            return {
                "current_scenario": scenario,
                "results": [forced_result.model_dump()],
                "diagnosis": None,
                "events": [make_event(
                    "execute", "result",
                    f"{forced.upper()}: {name} (mock_expected_status override)",
                    forced_result.model_dump(),
                )],
            }

    dut = get_dut(state, scenario=scenario)
    t0 = time.time()

    try:
        # 1. Apply stimulus
        await _apply_stimulus(dut, params)

        # 2. Capture waveforms
        duration = max(
            params.get("ramp_duration_s", 0) + params.get("hold_duration_s", 0) + 0.2,
            0.5,
        )
        analysis = ["mean", "max", "min", "rms",
                    "overshoot", "rise_time", "settling_time",
                    "thd", "rocof"]
        extra: dict = {}
        # Pass through optional capture-tuning params from the scenario
        for k in ("heal_target_param", "heal_target_threshold",
                  "rate_hz", "force_polling",
                  "trigger_source", "trigger_threshold", "trigger_edge",
                  "trigger_timeout_s"):
            if k in params:
                extra[k] = params[k]
        cap_result = await dut.capture(
            measurements, duration, analysis=analysis, **extra,
        )
        # P1 #10: liveness probe -- 3 consecutive flatlined captures
        # on a real-hardware backend abort the run.
        live_alert, live_reason = _liveness_observe(
            getattr(dut, "name", "?"),
            cap_result.get("statistics", []) if isinstance(cap_result, dict) else [],
        )
        if live_alert:
            return {
                "current_scenario": scenario,
                "results": [ScenarioResult(
                    scenario_id=sid, status="error",
                    duration_s=round(time.time() - t0, 3),
                    waveform_stats=[], fail_reason=live_reason,
                    retry_count=state.get("heal_retry_count", 0),
                ).model_dump()],
                "diagnosis": None,
                "error": live_reason,
                "events": [make_event(
                    "execute", "error",
                    f"LIVENESS ALERT: {live_reason}",
                )],
            }
        if "error" in cap_result:
            # Real-mode capture failure: do NOT fall back to PASS
            status, fail_reason = "error", f"capture failed: {cap_result['error']}"
            stats = []
        else:
            stats_raw = cap_result.get("statistics", [])
            stats = [WaveformStats(**s) for s in stats_raw]
            # 3. Evaluate pass/fail
            if measurements and not stats:
                status, fail_reason = "error", "capture returned no statistics"
            else:
                status, fail_reason = evaluate_rules(
                    rules, stats, scenario=scenario, strict=True,
                )

    except Exception as e:
        logger.exception(f"Scenario {sid} error")
        status, fail_reason = "error", str(e)
        stats = []

    elapsed = time.time() - t0
    # Pydantic validates ``status`` against the Literal in ScenarioResult.
    result = ScenarioResult(
        scenario_id=sid,
        status=status,  # type: ignore[arg-type]
        duration_s=round(elapsed, 3),
        waveform_stats=stats,
        fail_reason=fail_reason,
        retry_count=state.get("heal_retry_count", 0),
    )

    status_label = status.upper()
    msg = f"{status_label}: {name}"
    if fail_reason:
        msg += f" -- {fail_reason}"

    _heartbeat(node="execute_scenario", state={
        **state,
        "current_scenario": scenario,
        "results": [*state.get("results", []), result.model_dump()],
    })
    return {
        "current_scenario": scenario,
        "results": [result.model_dump()],
        "diagnosis": None,
        "events": [make_event("execute", "result", msg, result.model_dump())],
    }


# ----- Helpers -----

async def _apply_stimulus(dut, params: dict):
    """Apply test stimulus (ramp, step, fault) based on parameters.

    ``dut`` is any object with an ``execute(tool_name, tool_input)`` method --
    a DUTBackend (preferred) or, for backward compatibility, a raw
    HILToolExecutor. Fault templates use the same surface, so they keep
    working unchanged.
    """
    import asyncio

    # Preferred path: declarative fault template.
    template_name = params.get("fault_template")
    if template_name:
        template = get_template(template_name)
        if template is None:
            raise ValueError(f"Unknown fault_template: {template_name}")
        missing = validate_params(template, params)
        if missing:
            raise ValueError(
                f"fault_template={template_name} missing params: {missing}"
            )
        await template.apply(dut, params)
        return

    if "target_cell" in params:
        signal = f"V_cell_{params['target_cell']}"
    elif "target_sensor" in params:
        signal = params["target_sensor"]
    else:
        signal = None

    if "fault_voltage" in params and signal:
        normal = params.get("normal_voltage", 3.6)
        fault = params["fault_voltage"]
        dur = params.get("ramp_duration_s", 0.2)
        await dut.execute("hil_signal_write", {"signal": signal, "value": normal})
        await asyncio.sleep(0.05)
        await dut.execute("hil_signal_write", {
            "signal": signal, "waveform": "ramp",
            "start_value": normal, "end_value": fault, "duration_s": dur,
        })
        await asyncio.sleep(dur + 0.05)

    elif "test_voltage" in params and signal:
        v = params["test_voltage"]
        hold = params.get("hold_duration_s", 1.0)
        await dut.execute("hil_signal_write", {"signal": signal, "value": v})
        await asyncio.sleep(hold)

    elif "target_cells" in params:
        for cell in params["target_cells"]:
            sig = f"V_cell_{cell}"
            await dut.execute("hil_signal_write", {
                "signal": sig, "waveform": "ramp",
                "start_value": params.get("normal_voltage", 3.6),
                "end_value": params.get("fault_voltage", 4.5),
                "duration_s": params.get("ramp_duration_s", 0.2),
            })
        await asyncio.sleep(params.get("ramp_duration_s", 0.2) + 0.05)

    elif "fault_type" in params:
        await dut.execute("hil_fault_inject", {
            "fault_type": params["fault_type"],
            "target": params.get("target_sensor", ""),
            "parameters": params,
        })
        await asyncio.sleep(0.5)


# Legacy _evaluate() removed -- rule dispatch lives in src/evaluator.py.
# The 4 originally-hardcoded rules are now registered handlers there,
# joined by ~60 additional rules that previously passed silently.
