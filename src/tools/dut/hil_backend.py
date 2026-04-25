"""HIL backend -- wraps the existing HILToolExecutor.

Preserves current behavior bit-for-bit: every method dispatches to the
matching ``HILToolExecutor.execute("hil_*", ...)`` call. Calibration
writes have no native HIL path, so ``write_calibration`` falls through
to ``set_scada_input_value`` via ``hil_signal_write`` -- on real HIL404
this maps to SCADA tunables (P_ref / J / D / Kv per
docs/REAL_TYPHOON_BRINGUP.md).
"""

from __future__ import annotations

from typing import Any

from ..hil_tools import HILToolExecutor
from .base import BaseBackend


class HILBackend(BaseBackend):
    name = "hil"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._hil = HILToolExecutor()

    @property
    def hil(self) -> HILToolExecutor:
        return self._hil

    async def control(self, action: str, **kwargs: Any) -> dict:
        payload: dict[str, Any] = {"action": action}
        payload.update(kwargs)
        async with self.lock():
            return await self._hil.execute("hil_control", payload)

    async def write_signal(self, signal: str, **kwargs: Any) -> dict:
        payload: dict[str, Any] = {"signal": signal}
        payload.update(kwargs)
        async with self.lock():
            return await self._hil.execute("hil_signal_write", payload)

    async def read_signal(self, signals: list[str]) -> dict:
        async with self.lock():
            return await self._hil.execute("hil_signal_read", {"signals": signals})

    async def capture(
        self,
        signals: list[str],
        duration_s: float,
        analysis: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        payload: dict[str, Any] = {
            "signals": signals,
            "duration_s": duration_s,
        }
        if analysis is not None:
            payload["analysis"] = analysis
        payload.update(kwargs)
        async with self.lock():
            return await self._hil.execute("hil_capture", payload)

    async def fault_inject(
        self, fault_type: str, target: str, parameters: dict
    ) -> dict:
        async with self.lock():
            return await self._hil.execute("hil_fault_inject", {
                "fault_type": fault_type,
                "target": target,
                "parameters": parameters or {},
            })

    async def write_calibration(self, param: str, value: float) -> dict:
        # HIL has no XCP. The closest equivalent is a SCADA input write,
        # which is already what ``hil_signal_write`` does for tunables on
        # real HIL404. Do NOT silently succeed if there is no such signal
        # -- surface the error so apply_fix logs it correctly.
        async with self.lock():
            result = await self._hil.execute("hil_signal_write", {
                "signal": param, "value": value,
            })
        if "error" in result:
            return {
                "error": (
                    f"HILBackend cannot write calibration '{param}': "
                    f"no SCADA input or source matches "
                    f"({result['error']}). Use HybridBackend with XCP."
                ),
                "unsupported": True,
            }
        return {"variable": param, "written_value": value, "status": "ok"}

    async def read_calibration(self, param: str) -> dict:
        async with self.lock():
            result = await self._hil.execute("hil_signal_read", {"signals": [param]})
        values = result.get("values", {}) or {}
        if param in values:
            return {"variable": param, "value": values[param]}
        return {"error": f"HILBackend: no signal '{param}'"}

    async def execute(self, tool_name: str, tool_input: dict) -> dict:
        # Pass HIL tools straight through to preserve any quirks the
        # existing fault_templates rely on. Calibration tools fall back
        # to BaseBackend.execute (which calls write_/read_calibration).
        if tool_name in (
            "hil_control",
            "hil_signal_write",
            "hil_signal_read",
            "hil_capture",
            "hil_fault_inject",
        ):
            async with self.lock():
                return await self._hil.execute(tool_name, tool_input)
        return await super().execute(tool_name, tool_input)
