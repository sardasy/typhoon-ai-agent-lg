"""
Fault injection template library.

Provides 5 reusable fault archetypes addressable by name. Scenarios opt in by
setting `parameters.fault_template` (new key) to one of the FAULT_TEMPLATES
keys; `execute_scenario._apply_stimulus` dispatches here before falling back
to its legacy parameter-key chain.

Each template is a pure async function of (hil_executor, params) that drives
existing HIL tool primitives (hil_signal_write, hil_fault_inject). Templates
never mutate plant-model parameters (CLAUDE.md safety rule #1).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


StimulusFn = Callable[[Any, dict], Awaitable[dict]]


@dataclass(frozen=True)
class FaultTemplate:
    """Metadata + dispatcher for a single fault archetype."""
    name: str
    description: str
    required_params: tuple[str, ...]
    apply: StimulusFn


# ---------------------------------------------------------------------------
# Individual template implementations
# ---------------------------------------------------------------------------

async def _overvoltage(hil: Any, params: dict) -> dict:
    """Ramp a target signal from its nominal value to an elevated fault value.

    params: signal, nominal_value, fault_value, ramp_duration_s, hold_after_s
    """
    signal = params["signal"]
    nominal = params.get("nominal_value", params.get("normal_voltage", 0.0))
    fault = params["fault_value"]
    ramp = params.get("ramp_duration_s", 0.2)
    hold = params.get("hold_after_s", 0.1)

    await hil.execute("hil_signal_write", {"signal": signal, "value": nominal})
    await asyncio.sleep(0.02)
    await hil.execute("hil_signal_write", {
        "signal": signal,
        "waveform": "ramp",
        "start_value": nominal,
        "end_value": fault,
        "duration_s": ramp,
    })
    await asyncio.sleep(ramp + hold)
    return {"template": "overvoltage", "signal": signal, "peak": fault}


async def _undervoltage(hil: Any, params: dict) -> dict:
    """Ramp a target signal down to a depressed fault value."""
    signal = params["signal"]
    nominal = params.get("nominal_value", params.get("normal_voltage", 0.0))
    fault = params["fault_value"]
    ramp = params.get("ramp_duration_s", 0.2)
    hold = params.get("hold_after_s", 0.1)

    await hil.execute("hil_signal_write", {"signal": signal, "value": nominal})
    await asyncio.sleep(0.02)
    await hil.execute("hil_signal_write", {
        "signal": signal,
        "waveform": "ramp",
        "start_value": nominal,
        "end_value": fault,
        "duration_s": ramp,
    })
    await asyncio.sleep(ramp + hold)
    return {"template": "undervoltage", "signal": signal, "trough": fault}


async def _short_circuit(hil: Any, params: dict) -> dict:
    """Force a power-electronics switching block ON to emulate a short."""
    target = params["switch_name"]
    settle = params.get("hold_after_s", 0.2)
    result = await hil.execute("hil_fault_inject", {
        "fault_type": "switch_short",
        "target": target,
        "parameters": params,
    })
    await asyncio.sleep(settle)
    return {"template": "short_circuit", "target": target, "inject_result": result}


async def _open_circuit(hil: Any, params: dict) -> dict:
    """Force a power-electronics switching block OFF to emulate an open."""
    target = params["switch_name"]
    settle = params.get("hold_after_s", 0.2)
    result = await hil.execute("hil_fault_inject", {
        "fault_type": "switch_open",
        "target": target,
        "parameters": params,
    })
    await asyncio.sleep(settle)
    return {"template": "open_circuit", "target": target, "inject_result": result}


async def _frequency_deviation(hil: Any, params: dict) -> dict:
    """Shift a sine source's frequency to a deviated setpoint.

    Used for grid frequency excursion tests (IEEE 1547, IEC 61727).
    """
    signal = params["signal"]
    amplitude = params.get("amplitude", params.get("rms", 0.0))
    deviated_hz = params["deviated_frequency_hz"]
    hold = params.get("hold_after_s", 0.3)

    await hil.execute("hil_signal_write", {
        "signal": signal,
        "waveform": "sine",
        "value": amplitude,
        "frequency_hz": deviated_hz,
    })
    await asyncio.sleep(hold)
    return {
        "template": "frequency_deviation",
        "signal": signal,
        "frequency_hz": deviated_hz,
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FAULT_TEMPLATES: dict[str, FaultTemplate] = {
    "overvoltage": FaultTemplate(
        name="overvoltage",
        description="Ramp a signal above its nominal range to trigger OVP.",
        required_params=("signal", "fault_value"),
        apply=_overvoltage,
    ),
    "undervoltage": FaultTemplate(
        name="undervoltage",
        description="Ramp a signal below its nominal range to trigger UVP.",
        required_params=("signal", "fault_value"),
        apply=_undervoltage,
    ),
    "short_circuit": FaultTemplate(
        name="short_circuit",
        description="Force a switching block ON to emulate a short circuit.",
        required_params=("switch_name",),
        apply=_short_circuit,
    ),
    "open_circuit": FaultTemplate(
        name="open_circuit",
        description="Force a switching block OFF to emulate an open circuit.",
        required_params=("switch_name",),
        apply=_open_circuit,
    ),
    "frequency_deviation": FaultTemplate(
        name="frequency_deviation",
        description="Shift a sine source frequency for grid excursion tests.",
        required_params=("signal", "deviated_frequency_hz"),
        apply=_frequency_deviation,
    ),
}


def get_template(name: str) -> FaultTemplate | None:
    """Look up a template by name; None if not registered."""
    return FAULT_TEMPLATES.get(name)


def validate_params(template: FaultTemplate, params: dict) -> list[str]:
    """Return a list of missing required parameter names (empty = valid)."""
    return [k for k in template.required_params if k not in params]
