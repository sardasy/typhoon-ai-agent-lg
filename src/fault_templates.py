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
import math
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
    """Ramp target signal(s) from nominal to an elevated fault value.

    Supports single ``signal`` or 3-phase ``signal_ac_sources`` with
    ``fault_voltage_pu`` (per-unit of nominal, max 1.25 for test margin).
    """
    signals = params.get("signal_ac_sources", [params["signal"]] if "signal" in params else [])
    if not signals:
        raise ValueError("overvoltage requires 'signal' or 'signal_ac_sources'")

    nominal = params.get("nominal_voltage_peak", params.get("nominal_value", params.get("normal_voltage", 325.27)))
    fault_pu = params.get("fault_voltage_pu")
    if fault_pu is not None:
        # IEEE 1547 OV2 threshold is 1.20 pu; allow up to 1.25 for test margin
        if fault_pu > 1.25:
            raise ValueError(f"fault_voltage_pu={fault_pu} exceeds max 1.25 (IEEE 1547 OV2=1.20 + margin)")
        fault = nominal * fault_pu
    else:
        fault = params["fault_value"]
    ramp = params.get("ramp_duration_s", 0.2)
    hold = params.get("hold_after_s", 0.1)

    for sig in signals:
        await hil.execute("hil_signal_write", {"signal": sig, "value": nominal})
    await asyncio.sleep(0.02)
    for sig in signals:
        await hil.execute("hil_signal_write", {
            "signal": sig,
            "waveform": "ramp",
            "start_value": nominal,
            "end_value": fault,
            "duration_s": ramp,
        })
    await asyncio.sleep(ramp + hold)
    return {"template": "overvoltage", "signals": signals, "peak": fault}


async def _undervoltage(hil: Any, params: dict) -> dict:
    """Ramp target signal(s) down to a depressed fault value.

    Supports single ``signal`` or 3-phase ``signal_ac_sources`` with
    ``fault_voltage_pu`` (per-unit of nominal, min 0.0).
    """
    signals = params.get("signal_ac_sources", [params["signal"]] if "signal" in params else [])
    if not signals:
        raise ValueError("undervoltage requires 'signal' or 'signal_ac_sources'")

    nominal = params.get("nominal_voltage_peak", params.get("nominal_value", params.get("normal_voltage", 325.27)))
    fault_pu = params.get("fault_voltage_pu")
    if fault_pu is not None:
        if fault_pu < 0.0 or fault_pu > 1.0:
            raise ValueError(f"fault_voltage_pu={fault_pu} must be 0.0-1.0 for undervoltage")
        fault = nominal * fault_pu
    else:
        fault = params["fault_value"]
    ramp = params.get("ramp_duration_s", 0.2)
    hold = params.get("hold_after_s", 0.1)

    for sig in signals:
        await hil.execute("hil_signal_write", {"signal": sig, "value": nominal})
    await asyncio.sleep(0.02)
    for sig in signals:
        await hil.execute("hil_signal_write", {
            "signal": sig,
            "waveform": "ramp",
            "start_value": nominal,
            "end_value": fault,
            "duration_s": ramp,
        })
    await asyncio.sleep(ramp + hold)
    return {"template": "undervoltage", "signals": signals, "trough": fault}


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
    Supports single signal or signal_ac_sources (3-phase).
    """
    signals = params.get("signal_ac_sources", [params["signal"]] if "signal" in params else [])
    if not signals:
        raise ValueError("frequency_deviation requires 'signal' or 'signal_ac_sources'")
    amplitude = params.get("amplitude_peak", params.get("amplitude", params.get("rms", 325.27)))
    deviated_hz = params["deviated_frequency_hz"]
    hold = params.get("hold_after_s", 0.3)

    # IEEE 1547 frequency bounds validation (scaled to 50Hz base)
    if not (40.0 <= deviated_hz <= 65.0):
        raise ValueError(f"deviated_frequency_hz={deviated_hz} outside safe range 40-65 Hz")

    phases = [0, 120, 240]
    for i, sig in enumerate(signals):
        phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig,
            "waveform": "sine",
            "value": amplitude,
            "frequency_hz": deviated_hz,
            "phase_deg": phase,
        })
    await asyncio.sleep(hold)
    return {
        "template": "frequency_deviation",
        "signals": signals,
        "frequency_hz": deviated_hz,
    }


async def _voltage_sag(hil: Any, params: dict) -> dict:
    """Apply a voltage sag (dip) for LVRT testing.

    Reduces AC source amplitude to sag_voltage_pu for a specified duration,
    then restores to nominal. IEEE 1547 VRT tests.
    """
    signals = params.get("signal_ac_sources", [params.get("signal", "Vsa")])
    nominal = params.get("nominal_voltage_peak", 325.27)
    sag_pu = params.get("sag_voltage_pu", 0.5)
    pre_fault = params.get("pre_fault_duration_s", 1.0)
    fault_dur = params.get("fault_duration_s", 0.16)
    post_fault = params.get("post_fault_duration_s", 1.0)

    if not (0.0 <= sag_pu <= 1.0):
        raise ValueError(f"sag_voltage_pu={sag_pu} must be 0.0-1.0")

    sag_value = nominal * sag_pu
    phases = [0, 120, 240]

    # Pre-fault: nominal operation
    for i, sig in enumerate(signals):
        phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": nominal, "frequency_hz": 50, "phase_deg": phase,
        })
    await asyncio.sleep(pre_fault)

    # Fault: reduce amplitude
    for i, sig in enumerate(signals):
        phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": sag_value, "frequency_hz": 50, "phase_deg": phase,
        })
    await asyncio.sleep(fault_dur)

    # Post-fault: restore nominal
    for i, sig in enumerate(signals):
        phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": nominal, "frequency_hz": 50, "phase_deg": phase,
        })
    await asyncio.sleep(post_fault)

    return {"template": "voltage_sag", "sag_pu": sag_pu, "duration_s": fault_dur}


async def _voltage_swell(hil: Any, params: dict) -> dict:
    """Apply a voltage swell for HVRT testing.

    Increases AC source amplitude to swell_voltage_pu for a specified
    duration, then restores to nominal. IEEE 1547 VRT tests.
    """
    signals = params.get("signal_ac_sources", [params.get("signal", "Vsa")])
    nominal = params.get("nominal_voltage_peak", 325.27)
    swell_pu = params.get("swell_voltage_pu", 1.15)
    pre_fault = params.get("pre_fault_duration_s", 1.0)
    fault_dur = params.get("fault_duration_s", 12.0)
    post_fault = params.get("post_fault_duration_s", 1.0)

    if not (1.0 <= swell_pu <= 1.25):
        raise ValueError(f"swell_voltage_pu={swell_pu} must be 1.0-1.25 (IEEE 1547 max 1.20 pu + margin)")

    swell_value = nominal * swell_pu
    phases = [0, 120, 240]

    # Pre-fault
    for i, sig in enumerate(signals):
        phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": nominal, "frequency_hz": 50, "phase_deg": phase,
        })
    await asyncio.sleep(pre_fault)

    # Fault: increase amplitude
    for i, sig in enumerate(signals):
        phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": swell_value, "frequency_hz": 50, "phase_deg": phase,
        })
    await asyncio.sleep(fault_dur)

    # Post-fault: restore
    for i, sig in enumerate(signals):
        phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": nominal, "frequency_hz": 50, "phase_deg": phase,
        })
    await asyncio.sleep(post_fault)

    return {"template": "voltage_swell", "swell_pu": swell_pu, "duration_s": fault_dur}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FAULT_TEMPLATES: dict[str, FaultTemplate] = {
    "overvoltage": FaultTemplate(
        name="overvoltage",
        description="Ramp signal(s) above nominal range to trigger OVP.",
        required_params=(),  # validated inside _overvoltage (signal OR signal_ac_sources)
        apply=_overvoltage,
    ),
    "undervoltage": FaultTemplate(
        name="undervoltage",
        description="Ramp signal(s) below nominal range to trigger UVP.",
        required_params=(),  # validated inside _undervoltage
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
        description="Shift sine source frequency for grid excursion tests.",
        required_params=("deviated_frequency_hz",),  # signal OR signal_ac_sources validated inside
        apply=_frequency_deviation,
    ),
    "voltage_sag": FaultTemplate(
        name="voltage_sag",
        description="Apply voltage dip for LVRT testing (IEEE 1547 VRT).",
        required_params=(),
        apply=_voltage_sag,
    ),
    "voltage_swell": FaultTemplate(
        name="voltage_swell",
        description="Apply voltage swell for HVRT testing (IEEE 1547 VRT).",
        required_params=(),
        apply=_voltage_swell,
    ),
    # IEEE 2800 GFM templates -- defined below
    "vsm_steady_state": None,  # populated after async fns are defined
    "vsm_pref_step": None,
    "phase_jump": None,
}


# ---------------------------------------------------------------------------
# VSM-specific templates (IEEE 2800)
# ---------------------------------------------------------------------------

# Maximum allowed sleep durations (defensive timeouts to prevent test hangs).
# Settling at most 30 s is plenty for any GFM transient; capture windows
# should never exceed 10 s in our scenarios.
_MAX_SETTLE_S = 30.0
_MAX_CAPTURE_S = 10.0


async def _safe_sleep(seconds: float, *, hard_max: float) -> None:
    """asyncio.sleep with a hard upper bound to guard against bad params."""
    if seconds < 0:
        raise ValueError(f"sleep duration {seconds}s must be non-negative")
    bounded = min(float(seconds), hard_max)
    await asyncio.wait_for(asyncio.sleep(bounded), timeout=bounded + 1.0)


async def _grid_init(hil: Any, params: dict) -> None:
    """Optional pre-stimulus: energise an AC source (and DC bus if given).

    Schematics such as 3ph_inverter.tse leave the Three Phase Voltage Source
    at its class default (``init_rms_value=0.0``, ``init_frequency=50.0``),
    so VSM templates that only write SCADA inputs end up testing against a
    dead grid. Scenarios opt in by adding a ``grid_init`` dict:

        grid_init:
          source:    "Vgrid"   # name of the 3-phase source (required)
          rms:       220.0     # line-to-neutral RMS voltage (required)
          frequency: 60.0      # Hz (required)
          phase:     0.0       # degrees, default 0
          dc_source: "Vdc_link"  # optional
          dc_value:  700.0     # optional constant for the DC source
          settle_s:  0.2       # optional post-init settle, default 0.2

    No-op when ``grid_init`` is absent, so every existing scenario still
    behaves identically.
    """
    init = params.get("grid_init")
    if not init:
        return

    source = init.get("source")
    rms = init.get("rms")
    freq = init.get("frequency")
    if not source or rms is None or freq is None:
        raise ValueError(
            "grid_init requires 'source', 'rms', and 'frequency'; "
            f"got {init!r}"
        )

    # hil_signal_write's sine path expects peak ``value`` and computes
    # rms = value / sqrt(2) internally; convert from YAML-friendly RMS.
    amp = float(rms) * math.sqrt(2)
    phase = float(init.get("phase", 0.0))

    await hil.execute("hil_signal_write", {
        "signal": source,
        "waveform": "sine",
        "value": amp,
        "frequency_hz": float(freq),
        "phase_deg": phase,
    })

    dc_source = init.get("dc_source")
    dc_value = init.get("dc_value")
    if dc_source is not None and dc_value is not None:
        await hil.execute("hil_signal_write", {
            "signal": dc_source,
            "value": float(dc_value),
        })

    settle_s = float(init.get("settle_s", 0.2))
    await _safe_sleep(settle_s, hard_max=_MAX_SETTLE_S)


async def _vsm_steady_state(hil: Any, params: dict) -> dict:
    """Configure VSM SCADA inputs and let the system reach steady state."""
    pref = params.get("Pref_w", 0.0)
    qref = params.get("Qref_var", 0.0)
    j = params.get("J")
    d = params.get("D")
    kv = params.get("Kv")
    settle_s = params.get("settle_s", 2.0)

    await _grid_init(hil, params)

    if j is not None:
        await hil.execute("hil_signal_write", {"signal": "J", "value": j})
    if d is not None:
        await hil.execute("hil_signal_write", {"signal": "D", "value": d})
    if kv is not None:
        await hil.execute("hil_signal_write", {"signal": "Kv", "value": kv})
    await hil.execute("hil_signal_write", {"signal": "P_ref", "value": pref})
    await hil.execute("hil_signal_write", {"signal": "Q_ref", "value": qref})
    await _safe_sleep(settle_s, hard_max=_MAX_SETTLE_S)
    return {"template": "vsm_steady_state", "P_ref": pref, "Q_ref": qref}


async def _vsm_pref_step(hil: Any, params: dict) -> dict:
    """Pre-load Pref to initial value, settle, then step to target value.

    Used to characterise inertia response under different J values.
    """
    p_initial = params.get("Pref_initial_w", 2000.0)
    p_step = params.get("Pref_step_w", 8000.0)
    j = params.get("J", 0.3)
    d = params.get("D", 10.0)
    pre_step_s = params.get("pre_step_s", 3.0)
    capture_s = params.get("capture_s", 2.5)

    await _grid_init(hil, params)

    await hil.execute("hil_signal_write", {"signal": "J", "value": j})
    await hil.execute("hil_signal_write", {"signal": "D", "value": d})
    await hil.execute("hil_signal_write", {"signal": "P_ref", "value": p_initial})
    await _safe_sleep(pre_step_s, hard_max=_MAX_SETTLE_S)
    await hil.execute("hil_signal_write", {"signal": "P_ref", "value": p_step})
    await _safe_sleep(capture_s, hard_max=_MAX_CAPTURE_S)
    return {
        "template": "vsm_pref_step",
        "Pref_initial": p_initial,
        "Pref_step": p_step,
        "J": j, "D": d,
    }


async def _phase_jump(hil: Any, params: dict) -> dict:
    """Apply a sudden phase angle step to a 3-phase grid source.

    IEEE 2800 §7.3 mandates GFM IBR survival up to ±25°. We allow the test
    bench to push slightly beyond (±30°) for boundary characterisation, but
    reject anything more aggressive as it falls outside the standard scope.
    """
    signals = params.get("signal_ac_sources", [params.get("signal", "Vgrid")])
    nominal = params.get("nominal_voltage_peak", 325.27)
    f = params.get("nominal_frequency_hz", 50.0)
    phase_step_deg = params.get("phase_step_deg", 10.0)
    pre_jump_s = params.get("pre_jump_s", 2.0)
    post_jump_s = params.get("post_jump_s", 1.5)
    pref = params.get("Pref_w")

    # IEEE 2800 §7.3 compliance band; allow small margin for boundary tests
    if not (-30.0 <= phase_step_deg <= 30.0):
        raise ValueError(
            f"phase_step_deg={phase_step_deg} outside ±30° "
            "(IEEE 2800 §7.3 requires survival up to ±25°)"
        )

    if pref is not None:
        await hil.execute("hil_signal_write", {"signal": "P_ref", "value": pref})

    phases = [0, 120, 240]
    for i, sig in enumerate(signals):
        base_phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": nominal, "frequency_hz": f, "phase_deg": base_phase,
        })
    await _safe_sleep(pre_jump_s, hard_max=_MAX_SETTLE_S)

    for i, sig in enumerate(signals):
        base_phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": nominal, "frequency_hz": f,
            "phase_deg": base_phase + phase_step_deg,
        })
    await _safe_sleep(post_jump_s, hard_max=_MAX_CAPTURE_S)

    for i, sig in enumerate(signals):
        base_phase = phases[i] if i < len(phases) else 0
        await hil.execute("hil_signal_write", {
            "signal": sig, "waveform": "sine",
            "value": nominal, "frequency_hz": f, "phase_deg": base_phase,
        })

    return {"template": "phase_jump", "phase_step_deg": phase_step_deg}


# Wire the new templates into the registry now that their async fns exist.
FAULT_TEMPLATES["vsm_steady_state"] = FaultTemplate(
    name="vsm_steady_state",
    description="Drive VSM inverter to steady-state Pref/Qref (IEEE 2800).",
    required_params=(),
    apply=_vsm_steady_state,
)
FAULT_TEMPLATES["vsm_pref_step"] = FaultTemplate(
    name="vsm_pref_step",
    description="Apply Pref step on VSM inverter (IEEE 2800 §7.2.2).",
    required_params=(),
    apply=_vsm_pref_step,
)
FAULT_TEMPLATES["phase_jump"] = FaultTemplate(
    name="phase_jump",
    description="Apply phase angle step to grid source (IEEE 2800 §7.3).",
    required_params=(),
    apply=_phase_jump,
)


def get_template(name: str) -> FaultTemplate | None:
    """Look up a template by name; None if not registered."""
    return FAULT_TEMPLATES.get(name)


def validate_params(template: FaultTemplate, params: dict) -> list[str]:
    """Return a list of missing required parameter names (empty = valid)."""
    return [k for k in template.required_params if k not in params]
