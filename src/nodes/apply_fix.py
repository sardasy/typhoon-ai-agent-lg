"""
Node: apply_fix

Applies the corrective action recommended by the analyzer.
Currently supports XCP calibration writes only.
After applying, the graph loops back to execute_scenario for re-test.
"""

from __future__ import annotations

import logging
from typing import Any

from ..constants import ACTION_XCP_CALIBRATION
from ..state import AgentState, make_event
from ..tools.xcp_tools import XCPToolExecutor
from ..twin import get_twin
from ..validator import SafetyConfig, Validator
from .load_model import get_dut

logger = logging.getLogger(__name__)

# Default validator: conservative SafetyConfig. ``apply_fix`` reads
# ``state["safety_config"]`` (set by ``load_model`` from
# ``configs/safety/<profile>.yaml``) when present, so per-model
# overlays kick in without code changes.
_default_validator = Validator()


def _validator_for(state: AgentState) -> Validator:
    overlay = state.get("safety_config")
    if isinstance(overlay, dict) and overlay:
        return Validator(SafetyConfig.from_overlay(overlay))
    return _default_validator


def get_xcp() -> XCPToolExecutor:
    """Backward-compat accessor.

    Returns the XCPToolExecutor of whichever backend is currently active
    (HybridBackend / XCPBackend). For HILBackend / MockBackend (which
    don't carry a real XCP session) returns a dedicated executor so the
    write path keeps working in legacy tests.
    """
    dut = get_dut(None)
    inner_xcp = getattr(dut, "xcp", None)
    if inner_xcp is not None and hasattr(inner_xcp, "_xcp"):
        return inner_xcp._xcp  # XCPBackend wraps the executor
    if isinstance(inner_xcp, XCPToolExecutor):
        return inner_xcp
    return XCPToolExecutor()


async def apply_fix(state: AgentState) -> dict[str, Any]:
    """Apply XCP calibration write, then increment retry counter."""

    diagnosis = state.get("diagnosis") or {}
    action_type = diagnosis.get("corrective_action_type", "")
    param = diagnosis.get("corrective_param", "")
    value = diagnosis.get("corrective_value")

    retry = state.get("heal_retry_count", 0) + 1

    if action_type != ACTION_XCP_CALIBRATION or not param or value is None:
        return {
            "heal_retry_count": retry,
            "events": [make_event("apply_fix", "action", f"No XCP fix to apply (type={action_type})")],
        }

    # Safety check (per-model overlay if state["safety_config"] set).
    check = _validator_for(state).validate("xcp_interface", {
        "action": "write", "variable": param, "value": value,
    })
    if not check.allowed:
        return {
            "heal_retry_count": retry,
            "events": [make_event("apply_fix", "error", f"BLOCKED: {check.reason}")],
        }

    # Apply calibration via the configured DUT backend, routed to the
    # scenario's device when multi-device mode is active (Phase 4-I).
    scen = state.get("current_scenario") or {}
    dut = get_dut(state, scenario=scen)
    result = await dut.write_calibration(param, value)

    msg = f"XCP write: {param} = {value} (retry #{retry})"
    if result.get("error"):
        msg = f"XCP write failed: {result['error']}"
    else:
        # Phase 4-C: keep the digital twin's calibration mirror in sync
        # so subsequent simulate_fix calls can detect no-op retries.
        if state.get("twin_enabled"):
            scen = state.get("current_scenario") or {}
            get_twin().commit(
                param, float(value),
                scenario_id=scen.get("scenario_id", ""),
            )

    return {
        "heal_retry_count": retry,
        "events": [make_event("apply_fix", "action", msg, result)],
    }
