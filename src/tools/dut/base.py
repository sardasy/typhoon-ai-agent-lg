"""DUTBackend protocol + BaseBackend with the execute() shim.

The protocol exposes a backend-neutral surface. The shim translates legacy
``execute(tool_name, tool_input)`` calls (used by ``src/fault_templates.py``
and existing tests) into the typed methods so callers do not need to be
rewritten.

Phase 4-F serializes concurrent HIL/XCP calls across domain agents
(the Typhoon HIL API is single-threaded). Phase 4-I generalizes that
into a *per-device* lock registry: scenarios targeting different
physical devices can run truly in parallel; only same-device calls
contend. The legacy module attribute ``HARDWARE_LOCK`` is preserved
for backward compat -- it is the lock for the ``"default"`` device.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable


# Per-device lock registry. Keyed by ``device_id`` string. Use
# :func:`get_hardware_lock` from inside a backend; never read
# ``_DEVICE_LOCKS`` directly.
_DEVICE_LOCKS: dict[str, asyncio.Lock] = {}


def get_hardware_lock(device_id: str = "default") -> asyncio.Lock:
    """Return (or lazily create) the asyncio lock for one device.

    Backends call this with their configured ``device_id`` so that
    same-device I/O serializes while different-device calls overlap.
    The lock is created on first request -- never await this function;
    it is sync.
    """
    lock = _DEVICE_LOCKS.get(device_id)
    if lock is None:
        lock = asyncio.Lock()
        _DEVICE_LOCKS[device_id] = lock
    return lock


# Backward-compat shim: callers that still import ``HARDWARE_LOCK``
# get the default-device lock. New code should call
# :func:`get_hardware_lock(device_id)`.
HARDWARE_LOCK = get_hardware_lock("default")


@runtime_checkable
class DUTBackend(Protocol):
    """Protocol every DUT backend implements."""

    name: str

    async def control(self, action: str, **kwargs: Any) -> dict: ...
    async def write_signal(self, signal: str, **kwargs: Any) -> dict: ...
    async def read_signal(self, signals: list[str]) -> dict: ...
    async def capture(
        self,
        signals: list[str],
        duration_s: float,
        analysis: list[str] | None = None,
        **kwargs: Any,
    ) -> dict: ...
    async def fault_inject(
        self, fault_type: str, target: str, parameters: dict
    ) -> dict: ...
    async def write_calibration(self, param: str, value: float) -> dict: ...
    async def read_calibration(self, param: str) -> dict: ...
    async def execute(self, tool_name: str, tool_input: dict) -> dict: ...


class BaseBackend(ABC):
    """ABC providing the legacy ``execute()`` dispatch shim.

    Subclasses implement the typed methods. Existing code that calls
    ``backend.execute("hil_signal_write", {...})`` is routed to the
    appropriate typed method automatically. Backends may override
    ``execute()`` if they need richer dispatch (HILBackend does).
    """

    name: str = "base"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = config or {}
        # Phase 4-I: which physical device this backend instance binds
        # to. ``"default"`` keeps the single-device behavior intact.
        self.device_id: str = self.config.get("device_id", "default")

    def lock(self) -> asyncio.Lock:
        """asyncio.Lock for this backend's device. Used by hardware
        subclasses to serialize concurrent I/O against the same
        physical device while letting different devices overlap."""
        return get_hardware_lock(self.device_id)

    # ----- Required typed methods --------------------------------------

    @abstractmethod
    async def control(self, action: str, **kwargs: Any) -> dict: ...

    @abstractmethod
    async def write_signal(self, signal: str, **kwargs: Any) -> dict: ...

    @abstractmethod
    async def read_signal(self, signals: list[str]) -> dict: ...

    @abstractmethod
    async def capture(
        self,
        signals: list[str],
        duration_s: float,
        analysis: list[str] | None = None,
        **kwargs: Any,
    ) -> dict: ...

    @abstractmethod
    async def fault_inject(
        self, fault_type: str, target: str, parameters: dict
    ) -> dict: ...

    @abstractmethod
    async def write_calibration(self, param: str, value: float) -> dict: ...

    @abstractmethod
    async def read_calibration(self, param: str) -> dict: ...

    # ----- Fault injection helpers (Mirim Syscon DUTInterface parity) --
    # These are concrete defaults so fault_harness scenarios work
    # uniformly across HIL / XCP / Hybrid / Mock without each backend
    # re-implementing them. Backends MAY override for richer behavior.

    async def inject_overvoltage(
        self, level_v: float, ramp_time_s: float = 0.5,
        target: str = "Vsa",
    ) -> dict:
        """Ramp ``target`` source to ``level_v`` over ``ramp_time_s``."""
        return await self.write_signal(
            target, waveform="ramp",
            start_value=0.0, end_value=level_v,
            duration_s=ramp_time_s,
        )

    async def inject_undervoltage(
        self, level_v: float, ramp_time_s: float = 0.5,
        target: str = "Vsa",
    ) -> dict:
        """Ramp ``target`` down to ``level_v``. Default target Vsa."""
        return await self.write_signal(
            target, waveform="ramp",
            start_value=325.27, end_value=level_v,
            duration_s=ramp_time_s,
        )

    async def inject_overcurrent(
        self, target_a: float, ramp_time_s: float = 0.2,
        load_signal: str = "load_resistance",
    ) -> dict:
        """Drive a load to provoke ``target_a`` current draw.

        Operator wires this to the actual load model; the default
        implementation just writes the requested target as a setpoint.
        """
        return await self.write_signal(load_signal, value=target_a)

    async def inject_source_loss(self, target: str = "Vsa") -> dict:
        """Drop the source amplitude to zero (supply loss)."""
        return await self.write_signal(target, value=0.0)

    async def inject_sensor_fault(
        self, signal: str, mode: str = "stuck", value: float = 0.0,
    ) -> dict:
        """Poison a sensor reading on the ECU side via XCP write to a
        ``FAULT_<signal>_<mode>`` calibration parameter.

        Modes: ``stuck`` (constant value), ``offset`` (additive),
        ``noise`` (random walk). The ECU firmware MUST cooperate by
        checking these calibration knobs at runtime.
        """
        param = f"FAULT_{signal}_{mode}"
        return await self.write_calibration(param, value)

    async def expect_trip(
        self, fault_flag_signal: str = "fault_flag",
        within_ms: float = 1000.0, poll_ms: float = 1.0,
    ) -> bool:
        """Poll ``fault_flag_signal`` until it goes high or timeout.

        Returns True if trip observed, False on timeout.
        """
        import asyncio
        elapsed_ms = 0.0
        while elapsed_ms < within_ms:
            res = await self.read_signal([fault_flag_signal])
            values = res.get("values", {}) if isinstance(res, dict) else {}
            if float(values.get(fault_flag_signal, 0.0)) > 0.5:
                return True
            await asyncio.sleep(poll_ms / 1000.0)
            elapsed_ms += poll_ms
        return False

    async def is_tripped(
        self, fault_flag_signal: str = "fault_flag",
    ) -> bool:
        """Single-shot check of ``fault_flag_signal``."""
        res = await self.read_signal([fault_flag_signal])
        values = res.get("values", {}) if isinstance(res, dict) else {}
        return float(values.get(fault_flag_signal, 0.0)) > 0.5

    async def clear_fault(
        self, fault_flag_signal: str = "fault_flag",
        clear_command: str = "fault_reset",
    ) -> dict:
        """Pulse the ``clear_command`` SCADA input then read back the
        fault flag to confirm cleared."""
        await self.write_signal(clear_command, value=1.0)
        # Some controllers self-clear; others need an edge.
        await self.write_signal(clear_command, value=0.0)
        return await self.read_signal([fault_flag_signal])

    # ----- Legacy dispatch shim ----------------------------------------

    async def execute(self, tool_name: str, tool_input: dict) -> dict:
        """Translate ``execute(tool_name, tool_input)`` to a typed call.

        Recognised tool names: hil_control, hil_signal_write,
        hil_signal_read, hil_capture, hil_fault_inject, xcp_interface.
        """
        try:
            if tool_name == "hil_control":
                return await self.control(**tool_input)
            if tool_name == "hil_signal_write":
                signal = tool_input.get("signal", "")
                rest = {k: v for k, v in tool_input.items() if k != "signal"}
                return await self.write_signal(signal, **rest)
            if tool_name == "hil_signal_read":
                return await self.read_signal(tool_input.get("signals", []))
            if tool_name == "hil_capture":
                signals = tool_input.get("signals", [])
                duration = tool_input.get("duration_s", 0.5)
                analysis = tool_input.get("analysis")
                rest = {
                    k: v
                    for k, v in tool_input.items()
                    if k not in ("signals", "duration_s", "analysis")
                }
                return await self.capture(
                    signals, duration, analysis=analysis, **rest
                )
            if tool_name == "hil_fault_inject":
                return await self.fault_inject(
                    tool_input.get("fault_type", ""),
                    tool_input.get("target", ""),
                    tool_input.get("parameters", {}) or {},
                )
            if tool_name == "xcp_interface":
                action = tool_input.get("action", "")
                if action == "write":
                    raw_value = tool_input.get("value")
                    if raw_value is None:
                        return {"error": "xcp_interface write missing value"}
                    return await self.write_calibration(
                        tool_input.get("variable", ""),
                        float(raw_value),
                    )
                if action == "read":
                    return await self.read_calibration(
                        tool_input.get("variable", "")
                    )
                # connect / disconnect / daq_* / list_measurements:
                # subclasses that care override execute().
                return {"status": "noop", "action": action}
            return {"error": f"Unknown tool: {tool_name}"}
        except NotImplementedError as exc:
            return {"error": f"{self.name}: {exc}", "unsupported": True}
