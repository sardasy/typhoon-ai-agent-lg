"""
Node: parse_tse

Parses a .tse Typhoon HIL model file (text DSL or XML format)
and extracts signals, topology, sources, SCADA inputs, and parameters.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

from ..state import AgentState, ParsedTSE, make_event

logger = logging.getLogger(__name__)

# Topology keywords in priority order (most specific first)
TOPOLOGY_KEYWORDS = [
    ("dab", ["dab", "dual_active_bridge", "dual active bridge"]),
    ("flyback", ["flyback"]),
    ("full_bridge", ["full_bridge", "full bridge", "h_bridge", "h bridge"]),
    ("inverter", ["inverter", "vsi", "three_phase", "three phase"]),
    ("buck", ["buck"]),
    ("boost", ["boost"]),
]

# Component type -> signal type mapping
SIGNAL_COMPONENT_MAP = {
    "core/voltage measurement": "analog",
    "core/current measurement": "analog",
    "core/probe": "analog",
    "core/digital probe": "digital",
}

SOURCE_TYPES = {"core/voltage source", "core/current source"}
SCADA_TYPES = {"core/scada input"}
POWER_COMPONENTS = {
    "core/boost", "core/buck", "core/h bridge", "core/inverter",
    "core/dab", "core/flyback", "core/full bridge",
    "core/single phase inverter", "core/three phase inverter",
}


def _detect_topology(components: list[dict[str, Any]], model_name: str) -> str:
    """Detect circuit topology from component types and model name."""
    all_text = " ".join(
        c.get("type", "").lower() + " " + c.get("name", "").lower()
        for c in components
    ) + " " + model_name.lower()

    for topo, keywords in TOPOLOGY_KEYWORDS:
        if any(kw in all_text for kw in keywords):
            return topo

    # Structural inference: inductor + diode + switch + capacitor
    type_set = {c.get("type", "").lower() for c in components}
    has_inductor = any("inductor" in t for t in type_set)
    has_diode = any("diode" in t for t in type_set)
    has_switch = any("mosfet" in t or "igbt" in t or "switch" in t for t in type_set)
    has_cap = any("capacitor" in t for t in type_set)
    if has_inductor and has_diode and has_switch and has_cap:
        return "boost"  # generic DC-DC fallback

    return "unknown"


def _parse_dsl(content: str) -> ParsedTSE:
    """Parse text DSL format (.tse version 4.2+)."""
    analog_signals: list[str] = []
    digital_signals: list[str] = []
    sources: dict[str, float] = {}
    scada_inputs: dict[str, float] = {}
    components: list[dict[str, Any]] = []
    control_params: dict[str, Any] = {}
    sim_time_step = 1e-6
    dsp_timer_periods = 100e-6
    model_name = ""

    # Extract model name
    m = re.search(r'model_name\s*=\s*"([^"]*)"', content)
    if m:
        model_name = m.group(1)

    # Extract simulation time step
    m = re.search(r'simulation_time_step\s*=\s*([\d.eE+-]+)', content)
    if m:
        sim_time_step = float(m.group(1))

    # Extract DSP timer periods
    m = re.search(r'dsp_timer_periods?\s*=\s*([\d.eE+-]+)', content)
    if m:
        dsp_timer_periods = float(m.group(1))

    # Parse component blocks
    comp_pattern = re.compile(
        r'\[component\s+"([^"]+)"\]\s*'
        r'(?:type\s*=\s*"([^"]*)")?.*?'
        r'(?=\[component|\[configuration|\Z)',
        re.DOTALL
    )
    for match in comp_pattern.finditer(content):
        name = match.group(1)
        comp_type = match.group(2) or ""
        block = match.group(0)
        comp = {"name": name, "type": comp_type}
        components.append(comp)

        type_lower = comp_type.lower()

        # Signal extraction
        if type_lower in SIGNAL_COMPONENT_MAP:
            sig_type = SIGNAL_COMPONENT_MAP[type_lower]
            if sig_type == "digital":
                digital_signals.append(name)
            else:
                analog_signals.append(name)

        # Source extraction
        if type_lower in {t.lower() for t in SOURCE_TYPES}:
            val_match = re.search(r'init_const_value\s*=\s*([\d.eE+-]+)', block)
            val = float(val_match.group(1)) if val_match else 0.0
            sources[name] = val

        # SCADA input extraction
        if type_lower in {t.lower() for t in SCADA_TYPES}:
            val_match = re.search(r'def_value\s*=\s*([\d.eE+-]+)', block)
            val = float(val_match.group(1)) if val_match else 0.0
            scada_inputs[name] = val

    # Extract control parameters from CODE model_init block
    init_block = re.search(r'CODE\s+model_init\s*\{([^}]*)\}', content, re.DOTALL)
    if init_block:
        for param_m in re.finditer(r'(\w+)\s*=\s*([\d.eE+-]+)', init_block.group(1)):
            key = param_m.group(1)
            if key.lower() in ("kp", "ki", "kd", "ts", "f_sw", "fsw", "freq"):
                control_params[key] = float(param_m.group(2))

    topology = _detect_topology(components, model_name)

    return ParsedTSE(
        model_name=model_name,
        topology=topology,
        fmt="dsl",
        analog_signals=analog_signals,
        digital_signals=digital_signals,
        sources=sources,
        scada_inputs=scada_inputs,
        sim_time_step=sim_time_step,
        dsp_timer_periods=dsp_timer_periods,
        control_params=control_params,
        components=components,
    )


def _parse_xml(content: str) -> ParsedTSE:
    """Parse XML format (.tse older versions)."""
    root = ET.fromstring(content)
    analog_signals: list[str] = []
    digital_signals: list[str] = []
    sources: dict[str, float] = {}
    scada_inputs: dict[str, float] = {}
    components: list[dict[str, Any]] = []
    control_params: dict[str, Any] = {}
    sim_time_step = 1e-6
    dsp_timer_periods = 100e-6
    model_name = root.get("name", "")

    for comp in root.iter("component"):
        name = comp.get("name", "")
        comp_type = comp.get("type", "")
        components.append({"name": name, "type": comp_type})
        type_lower = comp_type.lower()

        if type_lower in SIGNAL_COMPONENT_MAP:
            if SIGNAL_COMPONENT_MAP[type_lower] == "digital":
                digital_signals.append(name)
            else:
                analog_signals.append(name)

        if type_lower in {t.lower() for t in SOURCE_TYPES}:
            val_el = comp.find(".//property[@name='init_const_value']")
            val = float(val_el.get("value", "0")) if val_el is not None else 0.0
            sources[name] = val

        if type_lower in {t.lower() for t in SCADA_TYPES}:
            val_el = comp.find(".//property[@name='def_value']")
            val = float(val_el.get("value", "0")) if val_el is not None else 0.0
            scada_inputs[name] = val

    # Configuration
    cfg = root.find(".//configuration")
    if cfg is not None:
        ts_el = cfg.find(".//property[@name='simulation_time_step']")
        if ts_el is not None:
            sim_time_step = float(ts_el.get("value", "1e-6"))
        dsp_el = cfg.find(".//property[@name='dsp_timer_periods']")
        if dsp_el is not None:
            dsp_timer_periods = float(dsp_el.get("value", "100e-6"))

    topology = _detect_topology(components, model_name)

    return ParsedTSE(
        model_name=model_name,
        topology=topology,
        fmt="xml",
        analog_signals=analog_signals,
        digital_signals=digital_signals,
        sources=sources,
        scada_inputs=scada_inputs,
        sim_time_step=sim_time_step,
        dsp_timer_periods=dsp_timer_periods,
        control_params=control_params,
        components=components,
    )


async def parse_tse(state: AgentState) -> dict[str, Any]:
    """Parse a .tse model file and extract topology/signals/parameters."""
    content = state.get("tse_content", "")
    tse_path = state.get("tse_path", "unknown.tse")

    if not content.strip():
        return {
            "error": "No TSE content provided",
            "events": [make_event("parse_tse", "error", "No TSE content to parse")],
        }

    first_line = content.strip().split("\n", 1)[0].strip()

    try:
        if first_line.startswith("<?xml"):
            parsed = _parse_xml(content)
        else:
            parsed = _parse_dsl(content)
    except Exception as exc:
        logger.error("TSE parsing failed: %s", exc)
        return {
            "error": f"TSE parsing failed: {exc}",
            "events": [make_event("parse_tse", "error", f"Parse error: {exc}")],
        }

    sig_count = len(parsed.analog_signals) + len(parsed.digital_signals)
    msg = (
        f"Parsed {tse_path}: {sig_count} signals, "
        f"topology={parsed.topology}, fmt={parsed.fmt}"
    )
    logger.info(msg)

    return {
        "parsed_tse": parsed.model_dump(),
        "events": [make_event("parse_tse", "observation", msg, {
            "signal_count": sig_count,
            "topology": parsed.topology,
            "model_name": parsed.model_name,
        })],
    }
