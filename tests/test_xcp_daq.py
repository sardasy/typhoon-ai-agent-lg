"""Tests for Phase 4-E XCPBackend DAQ capture."""

from __future__ import annotations

import pytest

from src.tools.dut import XCPBackend
from src.tools.xcp_tools import LAST_XCP_WRITE, XCPToolExecutor


# ---------------------------------------------------------------------------
# XCPToolExecutor._capture (mock path)
# ---------------------------------------------------------------------------

class TestCaptureMock:
    @pytest.mark.asyncio
    async def test_capture_returns_statistics_shape(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "capture",
            "signals": ["Vdc", "Idc"],
            "duration_s": 0.1,
            "analysis": ["mean", "max", "min", "rms"],
        })
        assert "statistics" in out
        assert {s["signal"] for s in out["statistics"]} == {"Vdc", "Idc"}
        for s in out["statistics"]:
            for k in ("mean", "max", "min", "rms"):
                assert k in s

    @pytest.mark.asyncio
    async def test_pre_heal_returns_flat_zeros(self):
        # Without a heal-target XCP write, mock returns zeros (forces fail).
        LAST_XCP_WRITE.pop("J", None)
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "capture",
            "signals": ["Vdc"], "duration_s": 0.1,
            "heal_target_param": "J",
            "heal_target_threshold": 0.3,
        })
        stats = out["statistics"][0]
        assert stats["mean"] == 0.0
        assert stats["max"] == 0.0

    @pytest.mark.asyncio
    async def test_post_heal_relay_signal_trips(self):
        # Heal-target met -> relay-like signals jump to 1.0.
        LAST_XCP_WRITE["J"] = 0.5
        try:
            x = XCPToolExecutor()
            out = await x.execute("xcp_interface", {
                "action": "capture",
                "signals": ["BMS_OVP_relay"], "duration_s": 0.1,
                "heal_target_param": "J",
                "heal_target_threshold": 0.3,
                "analysis": ["max", "rise_time"],
            })
            stats = out["statistics"][0]
            assert stats["max"] == pytest.approx(1.0)
            assert "rise_time_ms" in stats
        finally:
            LAST_XCP_WRITE.pop("J", None)

    @pytest.mark.asyncio
    async def test_capture_source_tag_is_xcp_mock(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "capture",
            "signals": ["x"], "duration_s": 0.05,
        })
        assert out["source"] == "xcp_mock"

    @pytest.mark.asyncio
    async def test_empty_signals_returns_error(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "capture", "signals": [], "duration_s": 0.1,
        })
        assert "error" in out


# ---------------------------------------------------------------------------
# XCPBackend.capture()
# ---------------------------------------------------------------------------

class TestXCPBackendCapture:
    @pytest.mark.asyncio
    async def test_capture_no_longer_raises(self):
        b = XCPBackend(config={"a2l_path": "dummy.a2l"})
        out = await b.capture(["Vdc"], 0.1)
        assert "statistics" in out

    @pytest.mark.asyncio
    async def test_capture_forwards_heal_kwargs(self):
        LAST_XCP_WRITE["J"] = 0.5
        try:
            b = XCPBackend(config={"a2l_path": "dummy.a2l"})
            out = await b.capture(
                ["BMS_OVP_relay"], 0.1,
                analysis=["max", "rise_time"],
                heal_target_param="J", heal_target_threshold=0.3,
            )
            assert out["statistics"][0]["max"] == pytest.approx(1.0)
        finally:
            LAST_XCP_WRITE.pop("J", None)

    @pytest.mark.asyncio
    async def test_capture_auto_connects(self):
        # XCPBackend auto-connects on first capture call (matching the
        # write_calibration / read_signal contract).
        b = XCPBackend(config={"a2l_path": "dummy.a2l"})
        assert not b.xcp.connected
        await b.capture(["x"], 0.05)
        assert b.xcp.connected


# ---------------------------------------------------------------------------
# Stat helpers
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_constant_signal_stats(self):
        out = XCPToolExecutor._compute_signal_stats(
            "x", [1.0] * 10, ["mean", "max", "min", "rms"], 100.0,
        )
        assert out["mean"] == pytest.approx(1.0)
        assert out["max"] == pytest.approx(1.0)
        assert out["min"] == pytest.approx(1.0)
        assert out["rms"] == pytest.approx(1.0)

    def test_step_signal_rise_time_present(self):
        # Step at index 5; with rate=100 Hz that's 50 ms.
        samples = [0.0] * 5 + [1.0] * 10
        out = XCPToolExecutor._compute_signal_stats(
            "x", samples, ["mean", "max", "rise_time"], 100.0,
        )
        assert out["rise_time_ms"] == pytest.approx(50.0)

    def test_empty_samples_returns_zeros(self):
        out = XCPToolExecutor._compute_signal_stats(
            "x", [], ["mean", "max"], 100.0,
        )
        assert out["mean"] == 0.0
        assert out["max"] == 0.0
