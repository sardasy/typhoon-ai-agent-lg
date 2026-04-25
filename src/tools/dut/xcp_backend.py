"""XCP backend -- real ECU calibration over pyXCP.

This backend can only do calibration reads/writes. Stimulus and capture
are not implemented (XCP does not produce waveforms; DAQ-based capture
is a future milestone). Use HybridBackend for tests that need both
HIL stimulus and XCP calibration.
"""

from __future__ import annotations

from typing import Any

from ..xcp_tools import XCPToolExecutor
from .base import BaseBackend


class XCPBackend(BaseBackend):
    name = "xcp"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._xcp = XCPToolExecutor()
        self._a2l_path: str = (config or {}).get("a2l_path", "")

    @property
    def xcp(self) -> XCPToolExecutor:
        return self._xcp

    async def _ensure_connected(self) -> None:
        if not self._xcp.connected:
            async with self.lock():
                # Recheck inside the lock to avoid racing connects.
                if not self._xcp.connected:
                    await self._xcp.execute(
                        "xcp_interface",
                        {"action": "connect", "a2l_path": self._a2l_path},
                    )

    async def control(self, action: str, **kwargs: Any) -> dict:
        # ECU is always running -- there is no model to load. We honour
        # "load" / "start" / "stop" as no-ops so the existing load_model
        # node does not need a special case.
        if action in ("load", "start", "stop"):
            return {"status": f"xcp_{action}_noop"}
        if action == "list_signals":
            await self._ensure_connected()
            return await self._xcp.execute(
                "xcp_interface", {"action": "list_measurements"}
            )
        if action == "status":
            return {"connected": self._xcp.connected, "a2l": self._a2l_path}
        return {"error": f"XCPBackend: unsupported control action '{action}'"}

    async def write_signal(self, signal: str, **kwargs: Any) -> dict:
        raise NotImplementedError(
            "XCPBackend does not support stimulus. Use HybridBackend."
        )

    async def read_signal(self, signals: list[str]) -> dict:
        await self._ensure_connected()
        out: dict[str, Any] = {}
        async with self.lock():
            for s in signals:
                res = await self._xcp.execute(
                    "xcp_interface", {"action": "read", "variable": s}
                )
                if "error" not in res:
                    out[s] = res.get("value")
        return {"values": out}

    async def capture(
        self,
        signals: list[str],
        duration_s: float,
        analysis: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Phase 4-E: real DAQ capture via pyxcp, mock fallback otherwise.

        The mock path mirrors HIL mock's heal-target convergence so
        ``--dut-backend xcp`` self-heal demos converge without an ECU.
        """
        await self._ensure_connected()
        payload: dict[str, Any] = {
            "action": "capture",
            "signals": list(signals),
            "duration_s": duration_s,
        }
        if analysis is not None:
            payload["analysis"] = list(analysis)
        # Forward heal_target_* and rate_hz so mock convergence works
        # and real DAQ honors the requested rate.
        for k in (
            "heal_target_param", "heal_target_threshold", "rate_hz",
        ):
            if k in kwargs:
                payload[k] = kwargs[k]
        return await self._xcp.execute("xcp_interface", payload)

    async def fault_inject(
        self, fault_type: str, target: str, parameters: dict
    ) -> dict:
        raise NotImplementedError(
            "XCPBackend cannot inject HIL faults. Use HybridBackend."
        )

    async def write_calibration(self, param: str, value: float) -> dict:
        await self._ensure_connected()
        async with self.lock():
            return await self._xcp.execute("xcp_interface", {
                "action": "write", "variable": param, "value": value,
            })

    async def read_calibration(self, param: str) -> dict:
        await self._ensure_connected()
        async with self.lock():
            return await self._xcp.execute("xcp_interface", {
                "action": "read", "variable": param,
            })
