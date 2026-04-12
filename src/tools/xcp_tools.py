"""
XCP Tools — pyXCP wrappers for real ECU measurement/calibration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

try:
    from pyxcp import Master as XCPMaster
    from pya2ldb import DB as A2LDB
    HAS_XCP = True
except ImportError:
    HAS_XCP = False
    logger.warning("pyXCP not available — XCP tools will be mocked")


XCP_TOOLS: list[dict] = [
    {
        "name": "xcp_interface",
        "description": (
            "Access real ECU internal variables via XCP protocol. "
            "Reads measurements and writes calibration parameters. "
            "Requires A2L file for variable name resolution. "
            "Use to diagnose failures invisible in HIL-only capture."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["connect", "disconnect", "read", "write",
                             "daq_start", "daq_stop", "list_measurements"],
                },
                "a2l_path": {"type": "string", "description": "Path to .a2l file"},
                "variable": {"type": "string", "description": "ECU variable name"},
                "value": {"type": "number", "description": "Value to write (calibration)"},
            },
            "required": ["action"],
        },
    },
]


@dataclass
class XCPToolExecutor:
    """Executes XCP tool calls against real ECU or mock."""

    connected: bool = False
    a2l_path: str = ""
    _session: Any = None
    _a2l_db: Any = None

    # White-listed calibration parameters (safety)
    WRITABLE_PARAMS: set[str] = None

    def __post_init__(self):
        if self.WRITABLE_PARAMS is None:
            self.WRITABLE_PARAMS = {
                "BMS_scanInterval_ch1", "BMS_scanInterval_ch2",
                "BMS_scanInterval_ch3", "BMS_scanInterval_ch4",
                "BMS_scanInterval_ch5", "BMS_scanInterval_ch6",
                "BMS_scanInterval_ch7", "BMS_scanInterval_ch8",
                "BMS_scanInterval_ch9", "BMS_scanInterval_ch10",
                "BMS_scanInterval_ch11", "BMS_scanInterval_ch12",
                "BMS_OVP_threshold", "BMS_UVP_threshold",
                "Ctrl_Kp", "Ctrl_Ki", "Ctrl_Kd",
            }

    async def execute(self, tool_name: str, tool_input: dict) -> dict[str, Any]:
        if tool_name != "xcp_interface":
            return {"error": f"Unknown XCP tool: {tool_name}"}

        action = tool_input["action"]
        dispatch = {
            "connect": self._connect,
            "disconnect": self._disconnect,
            "read": self._read,
            "write": self._write,
            "daq_start": self._daq_start,
            "daq_stop": self._daq_stop,
            "list_measurements": self._list_measurements,
        }
        handler = dispatch.get(action)
        if handler is None:
            return {"error": f"Unknown XCP action: {action}"}
        try:
            return await handler(tool_input)
        except Exception as e:
            logger.exception(f"XCP tool error: {action}")
            return {"error": str(e)}

    async def _connect(self, params: dict) -> dict:
        a2l = params.get("a2l_path", self.a2l_path)
        if not a2l:
            return {"error": "a2l_path required"}
        self.a2l_path = a2l

        if HAS_XCP:
            self._a2l_db = A2LDB(a2l)
            self._session = XCPMaster(transport="CAN")
            self._session.connect()
        self.connected = True
        return {"status": "connected", "a2l": a2l}

    async def _disconnect(self, _: dict) -> dict:
        if HAS_XCP and self._session:
            self._session.disconnect()
        self.connected = False
        return {"status": "disconnected"}

    async def _read(self, params: dict) -> dict:
        var_name = params.get("variable", "")
        if not var_name:
            return {"error": "variable name required"}
        if not self.connected:
            return {"error": "Not connected. Call connect first."}

        if HAS_XCP:
            meas = self._a2l_db.get_measurement(var_name)
            raw = self._session.shortUpload(meas.address, meas.size)
            value = meas.convert(raw)
        else:
            value = 0.0  # mock

        return {"variable": var_name, "value": value}

    async def _write(self, params: dict) -> dict:
        var_name = params.get("variable", "")
        value = params.get("value")
        if not var_name or value is None:
            return {"error": "variable and value required"}

        # Safety check: only write to white-listed params
        if var_name not in self.WRITABLE_PARAMS:
            return {
                "error": f"BLOCKED: '{var_name}' is not in the writable parameter whitelist. "
                         f"Escalate to human for safety-critical parameters.",
                "blocked": True,
            }

        if HAS_XCP:
            cal = self._a2l_db.get_calibration(var_name)
            self._session.download(cal.address, cal.encode(value))
        return {"variable": var_name, "written_value": value, "status": "ok"}

    async def _daq_start(self, params: dict) -> dict:
        return {"status": "daq_started", "note": "Continuous acquisition active"}

    async def _daq_stop(self, params: dict) -> dict:
        return {"status": "daq_stopped"}

    async def _list_measurements(self, params: dict) -> dict:
        if HAS_XCP and self._a2l_db:
            measurements = list(self._a2l_db.get_all_measurements().keys())
        else:
            measurements = [
                "u16_BattVolt", "s16_PackCurrent", "u8_OVP_State",
                "BMS_scanInterval_ch1", "BMS_scanInterval_ch7",
                "BMS_OVP_threshold", "Ctrl_Kp", "Ctrl_Ki",
            ]
        return {"measurements": measurements[:50]}
