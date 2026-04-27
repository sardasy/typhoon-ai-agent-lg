"""VHIL fault injection harness (Roadmap P1).

Extends ``src/fault_templates.py`` with **structured scenarios** that
inject controlled failures into the simulated plant + ECU surface,
then verify the controller's response. Two flavours:

1. **Plant-side faults** (already in fault_templates: voltage_sag,
   short_circuit, open_circuit, frequency_deviation). The harness
   wraps these in a ``FaultScenario`` lifecycle object that records
   pre-fault baseline / fault / recovery windows so the evaluator
   can compare each phase independently.

2. **ECU-side software faults** (NEW): bit-flip, stuck-at, sensor
   offset, CAN-message delay, scan-rate degradation. These poison
   the ECU's view of the world via XCP writes to test that the
   controller's plausibility checks fire.

Used by ``configs/scenarios_fault_*.yaml`` and the codegen pipeline.

Example::

    from src.fault_harness import FaultScenario, ECUSensorOffset
    fs = FaultScenario(
        name="cell_v_sensor_offset_5pct",
        plant_template="overvoltage",
        plant_params={"signal": "V_cell_3", "fault_voltage": 4.3, ...},
        ecu_fault=ECUSensorOffset(target="V_cell_3", offset_pct=5.0),
        verify_window_s=(0.2, 1.5),
    )

The harness is **not** wired into the LangGraph topology yet -- this
file defines the API. Integration with execute_scenario lands in a
follow-up PR.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# ECU-side software fault primitives
# ---------------------------------------------------------------------------

@dataclass
class ECUFault:
    """Base class for ECU-side software fault injection.

    Each subclass encodes the (target, magnitude, lifecycle) of a
    single poison applied to the ECU's view of the world. Applied
    via XCP write to a specially-whitelisted ``FAULT_*`` parameter
    (NOT the live calibration whitelist). See ``write_fault_param``.
    """
    target: str
    description: str = ""

    def to_xcp_writes(self) -> list[tuple[str, float]]:
        raise NotImplementedError


@dataclass
class ECUSensorOffset(ECUFault):
    """Add a fixed offset to a sensor reading.

    Applied as ``XCP write FAULT_<target>_offset = offset_pct``.
    The ECU firmware must check for this fault parameter and add it
    to the raw ADC reading at runtime.
    """
    offset_pct: float = 0.0

    def to_xcp_writes(self) -> list[tuple[str, float]]:
        return [(f"FAULT_{self.target}_offset_pct", self.offset_pct)]


@dataclass
class ECUStuckAt(ECUFault):
    """Force a sensor reading to a constant value (stuck-at fault).

    ``XCP write FAULT_<target>_stuck_value = value`` +
    ``FAULT_<target>_stuck_enable = 1``.
    """
    stuck_value: float = 0.0

    def to_xcp_writes(self) -> list[tuple[str, float]]:
        return [
            (f"FAULT_{self.target}_stuck_value", self.stuck_value),
            (f"FAULT_{self.target}_stuck_enable", 1.0),
        ]


@dataclass
class ECUScanRateDegrade(ECUFault):
    """Slow down the cyclic measurement task on this channel.

    ``XCP write FAULT_<target>_scan_period_ms = period_ms``. Tests
    timing-related plausibility checks (BMS scan interval ms).
    """
    period_ms: float = 0.0

    def to_xcp_writes(self) -> list[tuple[str, float]]:
        return [(f"FAULT_{self.target}_scan_period_ms", self.period_ms)]


@dataclass
class ECUCANDelay(ECUFault):
    """Inject a fixed delay on outbound CAN messages.

    Used to verify CAN-bus jitter tolerance and watchdog behavior.
    """
    delay_ms: float = 0.0

    def to_xcp_writes(self) -> list[tuple[str, float]]:
        return [(f"FAULT_CAN_{self.target}_delay_ms", self.delay_ms)]


# ---------------------------------------------------------------------------
# FaultScenario -- top-level lifecycle container
# ---------------------------------------------------------------------------

Phase = Literal["pre_fault", "fault", "recovery"]


@dataclass
class FaultScenario:
    """Combined plant + ECU fault scenario with explicit lifecycle phases.

    Attributes
    ----------
    name
        Stable identifier (used as scenario_id).
    plant_template
        Name of an entry in ``src.fault_templates.FAULT_TEMPLATES``
        (e.g. ``"overvoltage"``, ``"voltage_sag"``). When ``None``,
        only the ECU-side fault is applied.
    plant_params
        Parameters forwarded to the plant template.
    ecu_fault
        Optional ECU-side fault primitive applied via XCP write
        before the plant template runs.
    verify_window_s
        ``(start, stop)`` seconds bounding the recovery-phase
        evaluation window. Capture stats inside this slice are what
        the pass_fail rules grade.
    pass_fail_rules
        Standard evaluator rules to apply over the recovery window.
    standard_ref
        Standard / clause that justifies this scenario (for the
        Allure report and RAG retrieval).
    """
    name: str
    plant_template: str | None = None
    plant_params: dict[str, Any] = field(default_factory=dict)
    ecu_fault: ECUFault | None = None
    verify_window_s: tuple[float, float] = (0.0, 1.0)
    pass_fail_rules: dict[str, Any] = field(default_factory=dict)
    standard_ref: str = ""
    domain: str = "general"

    def to_yaml_dict(self) -> dict[str, Any]:
        """Render in the same shape ``configs/scenarios_*.yaml`` uses
        so existing ``plan_tests`` / ``execute_scenario`` consume it
        without changes."""
        params: dict[str, Any] = dict(self.plant_params)
        if self.plant_template:
            params["fault_template"] = self.plant_template
        if self.ecu_fault is not None:
            params["ecu_fault_writes"] = [
                {"param": p, "value": v}
                for p, v in self.ecu_fault.to_xcp_writes()
            ]
            params["ecu_fault_kind"] = type(self.ecu_fault).__name__
        params["verify_window_s"] = list(self.verify_window_s)
        return {
            "description": self.name,
            "category": "fault_injection",
            "standard_ref": self.standard_ref,
            "domain": self.domain,
            "parameters": params,
            "measurements": list(
                self.pass_fail_rules.get("_measurements", []),
            ),
            "pass_fail_rules": {
                k: v for k, v in self.pass_fail_rules.items()
                if not k.startswith("_")
            },
        }


# ---------------------------------------------------------------------------
# Library of canonical fault scenarios (extendable)
# ---------------------------------------------------------------------------

def bms_cell_sensor_offset(cell: int, offset_pct: float = 5.0) -> FaultScenario:
    """BMS protection plausibility: sensor offset injected on one cell."""
    target = f"V_cell_{cell}"
    return FaultScenario(
        name=f"bms_cell{cell}_sensor_offset_{offset_pct:.0f}pct",
        plant_template="overvoltage",
        plant_params={
            "signal": target, "fault_voltage": 4.30,
            "ramp_duration_s": 0.5, "hold_after_s": 0.5,
        },
        ecu_fault=ECUSensorOffset(target=target, offset_pct=offset_pct),
        verify_window_s=(0.6, 1.5),
        pass_fail_rules={
            "relay_must_trip": True,
            "response_time_max_ms": 200,
            "_measurements": [target, f"BMS_OVP_relay_ch{cell}"],
        },
        standard_ref="IEC 62619 7.2.1 (cell OVP plausibility)",
        domain="bms",
    )


def grid_freq_deviation_with_can_delay(
    deviated_hz: float = 49.0, can_delay_ms: float = 5.0,
) -> FaultScenario:
    """Combined plant + comm fault: under-frequency event AND late
    CAN status report. Tests that the inverter trips on its own
    measurement, not on stale supervisor commands."""
    return FaultScenario(
        name=f"grid_underfreq_{deviated_hz:.0f}hz_can_delay_{can_delay_ms:.0f}ms",
        plant_template="frequency_deviation",
        plant_params={
            "signal_ac_sources": ["Vsa", "Vsb", "Vsc"],
            "deviated_frequency_hz": deviated_hz,
            "amplitude_peak": 325.27,
            "hold_after_s": 1.0,
        },
        ecu_fault=ECUCANDelay(target="grid_status",
                                delay_ms=can_delay_ms),
        verify_window_s=(0.2, 1.5),
        pass_fail_rules={
            "frequency_threshold_hz": deviated_hz + 0.5,
            "must_not_trip": False,
            "_measurements": ["Vsa", "Pe", "Qe"],
        },
        standard_ref="IEEE 1547 6.5 + comm-fault tolerance",
        domain="grid",
    )


def pcs_dc_link_voltage_stuck(stuck_value: float = 0.0) -> FaultScenario:
    """PCS DC-DC fault tolerance: VDC sensor stuck at ``stuck_value``
    while the real plant ramps. Verifies controller's plausibility
    cross-check against current."""
    return FaultScenario(
        name=f"pcs_vdc_stuck_at_{stuck_value:.0f}",
        plant_template=None,  # plant unaffected
        ecu_fault=ECUStuckAt(target="VDC", stuck_value=stuck_value),
        verify_window_s=(0.0, 2.0),
        pass_fail_rules={
            "fault_detected": True,
            "controlled_shutdown": True,
            "_measurements": ["VDC", "IDC_1"],
        },
        standard_ref="UL 9540 sec.38 (sensor plausibility)",
        domain="pcs",
    )


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

CANONICAL_SCENARIOS = {
    "bms_cell1_sensor_offset_5pct": lambda: bms_cell_sensor_offset(1, 5.0),
    "grid_underfreq_49hz_can_5ms":
        lambda: grid_freq_deviation_with_can_delay(49.0, 5.0),
    "pcs_vdc_stuck_at_0": lambda: pcs_dc_link_voltage_stuck(0.0),
}


def all_scenarios() -> list[FaultScenario]:
    """Materialise the canonical library."""
    return [factory() for factory in CANONICAL_SCENARIOS.values()]
