"""
Node: execute_scenario

Picks the current scenario (by scenario_index), executes it on HIL,
captures waveforms, evaluates pass/fail, and appends the result.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ..fault_templates import get_template, validate_params
from ..state import AgentState, ScenarioResult, WaveformStats, make_event
from .load_model import get_hil

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

    hil = get_hil()
    t0 = time.time()

    try:
        # 1. Apply stimulus
        await _apply_stimulus(hil, params)

        # 2. Capture waveforms
        duration = max(
            params.get("ramp_duration_s", 0) + params.get("hold_duration_s", 0) + 0.2,
            0.5,
        )
        cap_kwargs = {
            "signals": measurements,
            "duration_s": duration,
            "analysis": ["mean", "max", "min", "rms", "overshoot", "rise_time"],
        }
        # Pass through optional capture-tuning params from the scenario
        for k in ("heal_target_param", "heal_target_threshold",
                  "rate_hz", "force_polling",
                  "trigger_source", "trigger_threshold", "trigger_edge",
                  "trigger_timeout_s"):
            if k in params:
                cap_kwargs[k] = params[k]
        cap_result = await hil.execute("hil_capture", cap_kwargs)
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
                status, fail_reason = _evaluate(rules, stats)

    except Exception as e:
        logger.exception(f"Scenario {sid} error")
        status, fail_reason = "error", str(e)
        stats = []

    elapsed = time.time() - t0
    result = ScenarioResult(
        scenario_id=sid,
        status=status,
        duration_s=round(elapsed, 3),
        waveform_stats=stats,
        fail_reason=fail_reason,
        retry_count=state.get("heal_retry_count", 0),
    )

    status_label = status.upper()
    msg = f"{status_label}: {name}"
    if fail_reason:
        msg += f" — {fail_reason}"

    return {
        "current_scenario": scenario,
        "results": [result.model_dump()],
        "diagnosis": None,
        "events": [make_event("execute", "result", msg, result.model_dump())],
    }


# ----- Helpers -----

async def _apply_stimulus(hil, params: dict):
    """Apply test stimulus (ramp, step, fault) based on parameters."""
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
        await template.apply(hil, params)
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
        await hil.execute("hil_signal_write", {"signal": signal, "value": normal})
        await asyncio.sleep(0.05)
        await hil.execute("hil_signal_write", {
            "signal": signal, "waveform": "ramp",
            "start_value": normal, "end_value": fault, "duration_s": dur,
        })
        await asyncio.sleep(dur + 0.05)

    elif "test_voltage" in params and signal:
        v = params["test_voltage"]
        hold = params.get("hold_duration_s", 1.0)
        await hil.execute("hil_signal_write", {"signal": signal, "value": v})
        await asyncio.sleep(hold)

    elif "target_cells" in params:
        for cell in params["target_cells"]:
            sig = f"V_cell_{cell}"
            await hil.execute("hil_signal_write", {
                "signal": sig, "waveform": "ramp",
                "start_value": params.get("normal_voltage", 3.6),
                "end_value": params.get("fault_voltage", 4.5),
                "duration_s": params.get("ramp_duration_s", 0.2),
            })
        await asyncio.sleep(params.get("ramp_duration_s", 0.2) + 0.05)

    elif "fault_type" in params:
        await hil.execute("hil_fault_inject", {
            "fault_type": params["fault_type"],
            "target": params.get("target_sensor", ""),
            "parameters": params,
        })
        await asyncio.sleep(0.5)


def _evaluate(
    rules: dict, stats: list[WaveformStats]
) -> tuple[str, str]:
    """Evaluate pass/fail rules. Returns (status, fail_reason)."""
    stats_map = {s.signal: s for s in stats}

    for key, val in rules.items():
        if key == "relay_must_trip":
            for name, s in stats_map.items():
                if "relay" in name.lower() and s.max < 0.5:
                    return "fail", "Protection relay did not trip"

        elif key == "relay_must_not_trip":
            for name, s in stats_map.items():
                if "relay" in name.lower() and s.max > 0.5:
                    return "fail", "Relay tripped at boundary (should not)"

        elif key == "response_time_max_ms":
            for name, s in stats_map.items():
                if "relay" in name.lower() and s.rise_time_ms is not None:
                    if s.rise_time_ms > val:
                        return "fail", f"Response time {s.rise_time_ms:.1f}ms > {val}ms limit"

        elif key == "overshoot_max_percent":
            for s in stats:
                if s.overshoot_percent is not None and s.overshoot_percent > val:
                    return "fail", f"{s.signal} overshoot {s.overshoot_percent:.1f}% > {val}%"

        elif key == "steady_state_error_max_percent":
            for s in stats:
                if "out" in s.signal.lower():
                    ref = rules.get("output_voltage_ref", s.mean) or s.mean
                    if ref > 0:
                        err = abs(s.mean - ref) / ref * 100
                        if err > val:
                            return "fail", f"SS error {err:.2f}% > {val}%"

    return "pass", ""
