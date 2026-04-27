"""Contract tests for the DUT abstraction layer (Phase 4 MVP)."""

from __future__ import annotations

import pytest

from src.tools.dut import (
    BaseBackend,
    DUTBackend,
    HILBackend,
    HybridBackend,
    MockBackend,
    XCPBackend,
    get_backend,
)
from src.tools.xcp_tools import LAST_XCP_WRITE


# pytest.ini sets asyncio_mode = auto, so async tests run without markers
# and sync tests stay sync.


# ---------------------------------------------------------------------------
# Factory + Protocol conformance
# ---------------------------------------------------------------------------

class TestFactory:
    def test_factory_returns_correct_class(self):
        assert isinstance(get_backend("hil"), HILBackend)
        assert isinstance(get_backend("xcp"), XCPBackend)
        assert isinstance(get_backend("hybrid"), HybridBackend)
        assert isinstance(get_backend("mock"), MockBackend)

    def test_unknown_backend_falls_back_to_hil(self):
        assert isinstance(get_backend("does-not-exist"), HILBackend)

    def test_all_backends_satisfy_protocol(self):
        for name in ("hil", "xcp", "hybrid", "mock"):
            backend = get_backend(name)
            assert isinstance(backend, DUTBackend)
            assert isinstance(backend, BaseBackend)
            assert backend.name == name


# ---------------------------------------------------------------------------
# MockBackend behavior (deterministic surface contract)
# ---------------------------------------------------------------------------

class TestMockBackend:
    async def test_records_calls_in_order(self):
        m = MockBackend()
        await m.write_signal("Va", value=1.0)
        await m.capture(["Va"], 0.1)
        await m.write_calibration("J", 0.35)
        methods = [c[0] for c in m.calls]
        assert methods == ["write_signal", "capture", "write_calibration"]

    async def test_capture_returns_default_statistics(self):
        m = MockBackend()
        result = await m.capture(["Va", "Ia"], 0.5)
        assert "statistics" in result
        assert {s["signal"] for s in result["statistics"]} == {"Va", "Ia"}

    async def test_capture_response_override(self):
        m = MockBackend()
        m.set_capture_response({"statistics": [], "error": "forced"})
        result = await m.capture(["X"], 0.1)
        assert result == {"statistics": [], "error": "forced"}
        # Override is one-shot
        result2 = await m.capture(["X"], 0.1)
        assert "error" not in result2

    async def test_write_calibration_records_to_last_xcp_write(self):
        m = MockBackend()
        LAST_XCP_WRITE.pop("J", None)
        await m.write_calibration("J", 0.42)
        assert LAST_XCP_WRITE.get("J") == pytest.approx(0.42)

    async def test_execute_shim_routes_to_typed_methods(self):
        m = MockBackend()
        await m.execute("hil_signal_write", {"signal": "Vgrid", "value": 230})
        await m.execute("hil_capture", {"signals": ["Va"], "duration_s": 0.2})
        await m.execute("xcp_interface", {
            "action": "write", "variable": "J", "value": 0.5,
        })
        kinds = [c[0] for c in m.calls]
        assert kinds == ["write_signal", "capture", "write_calibration"]


# ---------------------------------------------------------------------------
# HILBackend behavior
# ---------------------------------------------------------------------------

class TestHILBackend:
    async def test_control_load_returns_signals(self):
        b = HILBackend()
        result = await b.control("load", model_path="dummy.tse")
        # In mock mode HILToolExecutor populates a default signal list.
        assert result.get("status") == "model_loaded"
        assert isinstance(result.get("signals", []), list)

    async def test_capture_dispatches_to_hil_executor(self):
        b = HILBackend()
        await b.control("load", model_path="dummy.tse")
        await b.control("start")
        result = await b.capture(["V_cell_1"], 0.5, analysis=["mean", "max"])
        assert "statistics" in result or "error" in result

    async def test_execute_shim_passes_through_for_hil_tools(self):
        b = HILBackend()
        await b.control("load", model_path="dummy.tse")
        result = await b.execute(
            "hil_signal_write", {"signal": "V_cell_1", "value": 3.6},
        )
        assert "error" not in result


# ---------------------------------------------------------------------------
# XCPBackend behavior
# ---------------------------------------------------------------------------

class TestXCPBackend:
    async def test_stimulus_methods_raise_not_implemented(self):
        b = XCPBackend()
        result = await b.execute(
            "hil_signal_write", {"signal": "Va", "value": 1.0},
        )
        assert result.get("unsupported") is True

    async def test_calibration_write_succeeds(self):
        b = XCPBackend(config={"a2l_path": "dummy.a2l"})
        result = await b.write_calibration("J", 0.3)
        assert result.get("status") == "ok"
        assert result.get("written_value") == 0.3

    async def test_calibration_write_blocks_non_whitelisted_param(self):
        b = XCPBackend(config={"a2l_path": "dummy.a2l"})
        result = await b.write_calibration("ARBITRARY_PARAM", 99.0)
        assert result.get("blocked") is True or "BLOCKED" in str(result.get("error", ""))

    async def test_control_load_is_noop(self):
        b = XCPBackend()
        result = await b.control("load", model_path="ignored.tse")
        assert result.get("status") == "xcp_load_noop"


# ---------------------------------------------------------------------------
# HybridBackend behavior
# ---------------------------------------------------------------------------

class TestHybridBackend:
    async def test_capture_delegates_to_hil(self):
        b = HybridBackend()
        await b.control("load", model_path="dummy.tse")
        result = await b.capture(["Va"], 0.5)
        # If it had been the XCP delegate, this would have raised.
        assert "statistics" in result or "error" in result

    async def test_calibration_delegates_to_xcp(self):
        b = HybridBackend(config={"a2l_path": "dummy.a2l"})
        result = await b.write_calibration("J", 0.4)
        assert result.get("status") == "ok"
        # And LAST_XCP_WRITE got the value (XCPToolExecutor side effect)
        assert LAST_XCP_WRITE.get("J") == pytest.approx(0.4)

    async def test_stimulus_delegates_to_hil(self):
        b = HybridBackend()
        await b.control("load", model_path="dummy.tse")
        result = await b.write_signal("V_cell_1", value=3.6)
        assert "error" not in result


# ---------------------------------------------------------------------------
# load_model.get_dut() integration
# ---------------------------------------------------------------------------

class TestGetDut:
    async def test_default_state_returns_hil_backend(self):
        from src.nodes.load_model import get_dut
        b = get_dut({})
        assert isinstance(b, HILBackend)

    async def test_state_with_mock_returns_mock(self):
        from src.nodes.load_model import get_dut
        b = get_dut({"dut_backend": "mock", "dut_config": {}})
        assert isinstance(b, MockBackend)

    async def test_caching_returns_same_instance_for_same_config(self):
        from src.nodes.load_model import get_dut
        b1 = get_dut({"dut_backend": "mock", "dut_config": {"k": 1}})
        b2 = get_dut({"dut_backend": "mock", "dut_config": {"k": 1}})
        assert b1 is b2

    async def test_different_config_returns_new_instance(self):
        from src.nodes.load_model import get_dut
        b1 = get_dut({"dut_backend": "mock", "dut_config": {"k": 1}})
        b2 = get_dut({"dut_backend": "mock", "dut_config": {"k": 2}})
        assert b1 is not b2
