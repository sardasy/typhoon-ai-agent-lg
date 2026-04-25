"""MockBackend -- deterministic in-memory backend for tests.

Records every call to ``calls`` so tests can assert dispatch order.
``capture()`` returns a synthetic ``statistics`` payload that satisfies
the most common pass/fail rules; tests that need richer behavior can
inject results via ``set_capture_response``.
"""

from __future__ import annotations

from typing import Any

from ..xcp_tools import LAST_XCP_WRITE
from .base import BaseBackend


class MockBackend(BaseBackend):
    name = "mock"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.calls: list[tuple[str, dict]] = []
        self._signal_values: dict[str, float] = {}
        self._calibration: dict[str, float] = {}
        self._capture_override: dict | None = None
        # Mirror HILBackend's signal universe so signal_validator passes.
        self._signal_list: list[str] = list((config or {}).get("signals", []))

    # --- Test helpers ---------------------------------------------------

    def set_capture_response(self, payload: dict) -> None:
        """Force the next capture() call to return this dict verbatim."""
        self._capture_override = payload

    def set_signals(self, signals: list[str]) -> None:
        self._signal_list = list(signals)

    def _record(self, method: str, args: dict) -> None:
        self.calls.append((method, dict(args)))

    # --- Backend methods ------------------------------------------------

    async def control(self, action: str, **kwargs: Any) -> dict:
        self._record("control", {"action": action, **kwargs})
        if action == "load":
            return {
                "status": "model_loaded",
                "model_path": kwargs.get("model_path", ""),
                "signal_count": len(self._signal_list),
                "signals": self._signal_list,
            }
        if action == "list_signals":
            return {"signals": self._signal_list}
        if action == "status":
            return {"model_loaded": True, "simulation_running": True}
        return {"status": f"mock_{action}"}

    async def write_signal(self, signal: str, **kwargs: Any) -> dict:
        self._record("write_signal", {"signal": signal, **kwargs})
        if "value" in kwargs:
            self._signal_values[signal] = float(kwargs["value"])
        return {"signal": signal, "set_to": kwargs.get("value")}

    async def read_signal(self, signals: list[str]) -> dict:
        self._record("read_signal", {"signals": list(signals)})
        return {"values": {s: self._signal_values.get(s, 0.0) for s in signals}}

    async def capture(
        self,
        signals: list[str],
        duration_s: float,
        analysis: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        self._record("capture", {
            "signals": list(signals),
            "duration_s": duration_s,
            "analysis": list(analysis or []),
            **kwargs,
        })
        if self._capture_override is not None:
            payload = self._capture_override
            self._capture_override = None
            return payload
        stats = [
            {
                "signal": s, "mean": 0.0, "max": 1.0, "min": 0.0,
                "rms": 0.5, "overshoot_percent": 0.0,
                "rise_time_ms": 50.0, "settling_time_ms": 100.0,
            }
            for s in signals
        ]
        return {"statistics": stats, "duration_s": duration_s}

    async def fault_inject(
        self, fault_type: str, target: str, parameters: dict
    ) -> dict:
        self._record("fault_inject", {
            "fault_type": fault_type, "target": target,
            "parameters": dict(parameters or {}),
        })
        return {"status": "fault_injected", "type": fault_type, "target": target}

    async def write_calibration(self, param: str, value: float) -> dict:
        self._record("write_calibration", {"param": param, "value": value})
        self._calibration[param] = float(value)
        # Mirror XCPToolExecutor's side effect so mocked self-heal demos
        # keep converging.
        LAST_XCP_WRITE[param] = float(value)
        return {"variable": param, "written_value": value, "status": "ok"}

    async def read_calibration(self, param: str) -> dict:
        self._record("read_calibration", {"param": param})
        return {"variable": param, "value": self._calibration.get(param, 0.0)}
