"""Hybrid backend -- HIL stimulus + capture, real ECU calibration via XCP.

This is the practical real-world setup: the HIL device emulates the
plant (battery, grid, motor) while a real ECU under test exposes
calibration parameters over XCP. The agent treats both as one DUT.
"""

from __future__ import annotations

from typing import Any

from .base import BaseBackend
from .hil_backend import HILBackend
from .xcp_backend import XCPBackend


class HybridBackend(BaseBackend):
    name = "hybrid"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._hil = HILBackend(config)
        self._xcp = XCPBackend(config)

    @property
    def hil(self) -> HILBackend:
        return self._hil

    @property
    def xcp(self) -> XCPBackend:
        return self._xcp

    async def control(self, action: str, **kwargs: Any) -> dict:
        # Lifecycle is governed by HIL; XCP is connect-on-demand.
        return await self._hil.control(action, **kwargs)

    async def write_signal(self, signal: str, **kwargs: Any) -> dict:
        return await self._hil.write_signal(signal, **kwargs)

    async def read_signal(self, signals: list[str]) -> dict:
        return await self._hil.read_signal(signals)

    async def capture(
        self,
        signals: list[str],
        duration_s: float,
        analysis: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        return await self._hil.capture(
            signals, duration_s, analysis=analysis, **kwargs
        )

    async def fault_inject(
        self, fault_type: str, target: str, parameters: dict
    ) -> dict:
        return await self._hil.fault_inject(fault_type, target, parameters)

    async def write_calibration(self, param: str, value: float) -> dict:
        return await self._xcp.write_calibration(param, value)

    async def read_calibration(self, param: str) -> dict:
        return await self._xcp.read_calibration(param)
