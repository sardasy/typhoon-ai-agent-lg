"""
Node: apply_fix

Applies the corrective action recommended by the analyzer.
Currently supports XCP calibration writes only.
After applying, the graph loops back to execute_scenario for re-test.
"""

from __future__ import annotations

import logging
from typing import Any

from ..state import AgentState, make_event
from ..tools.xcp_tools import XCPToolExecutor
from ..validator import Validator, SafetyConfig

logger = logging.getLogger(__name__)

_xcp = XCPToolExecutor()
_validator = Validator()


def get_xcp() -> XCPToolExecutor:
    return _xcp


async def apply_fix(state: AgentState) -> dict[str, Any]:
    """Apply XCP calibration write, then increment retry counter."""

    diagnosis = state.get("diagnosis") or {}
    action_type = diagnosis.get("corrective_action_type", "")
    param = diagnosis.get("corrective_param", "")
    value = diagnosis.get("corrective_value")

    retry = state.get("heal_retry_count", 0) + 1

    if action_type != "xcp_calibration" or not param or value is None:
        return {
            "heal_retry_count": retry,
            "events": [make_event("apply_fix", "action", f"No XCP fix to apply (type={action_type})")],
        }

    # Safety check
    check = _validator.validate("xcp_interface", {
        "action": "write", "variable": param, "value": value,
    })
    if not check.allowed:
        return {
            "heal_retry_count": retry,
            "events": [make_event("apply_fix", "error", f"BLOCKED: {check.reason}")],
        }

    # Apply calibration
    xcp = get_xcp()
    if not xcp.connected:
        await xcp.execute("xcp_interface", {"action": "connect", "a2l_path": ""})

    result = await xcp.execute("xcp_interface", {
        "action": "write", "variable": param, "value": value,
    })

    msg = f"XCP write: {param} = {value} (retry #{retry})"
    if result.get("error"):
        msg = f"XCP write failed: {result['error']}"

    return {
        "heal_retry_count": retry,
        "events": [make_event("apply_fix", "action", msg, result)],
    }
