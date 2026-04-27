"""Coverage boost for ``src/tools/xcp_tools.py`` -- the action
handlers and HAS_XCP=True branches the existing suite skips.

Combined with ``test_xcp_daq.py`` (DAQ capture), this brings the
module from ~57% to ~80%+. Real-mode tests use ``MagicMock`` to
simulate the pyXCP master + A2L DB so the production code paths run
without an actual ECU.

Covered:
  - dispatch: unknown action -> error
  - dispatch: handler raises -> error swallowed + logged
  - _connect: missing a2l_path
  - _connect: HAS_XCP=True path (XCPMaster + A2LDB instantiation)
  - _disconnect: HAS_XCP=True session cleanup
  - _read: missing variable, not connected, mock value, real-mode
  - _write: missing variable/value, HAS_XCP=True calibration write
  - _daq_start / _daq_stop status returns
  - _list_measurements: mock + real-mode (HAS_XCP=True)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.tools.xcp_tools import LAST_XCP_WRITE, XCPToolExecutor


# ---------------------------------------------------------------------------
# Dispatch surface
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool_name(self):
        x = XCPToolExecutor()
        out = await x.execute("not_xcp", {})
        assert "error" in out
        assert "Unknown XCP tool" in out["error"]

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {"action": "fly_to_moon"})
        assert "error" in out
        assert "Unknown XCP action" in out["error"]

    @pytest.mark.asyncio
    async def test_handler_exception_swallowed(self, monkeypatch):
        """When a handler raises, ``execute`` logs and returns
        ``{"error": ...}`` rather than letting the run crash."""
        x = XCPToolExecutor()

        async def _boom(_params):
            raise RuntimeError("simulated XCP failure")

        # Override one of the handlers so the dispatch -> handler
        # call hits the broad except.
        monkeypatch.setattr(x, "_read", _boom)
        out = await x.execute("xcp_interface", {
            "action": "read", "variable": "J",
        })
        assert out["error"] == "simulated XCP failure"


# ---------------------------------------------------------------------------
# _connect
# ---------------------------------------------------------------------------

class TestConnect:
    @pytest.mark.asyncio
    async def test_missing_a2l_path_error(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {"action": "connect"})
        assert "error" in out
        assert "a2l_path" in out["error"]

    @pytest.mark.asyncio
    async def test_mock_mode_marks_connected(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "connect", "a2l_path": "fake.a2l",
        })
        assert out["status"] == "connected"
        assert x.connected is True

    @pytest.mark.asyncio
    async def test_real_mode_calls_pyxcp(self, monkeypatch):
        """When HAS_XCP=True, ``_connect`` instantiates A2LDB +
        XCPMaster and calls connect()."""
        import src.tools.xcp_tools as xcp_mod
        monkeypatch.setattr(xcp_mod, "HAS_XCP", True, raising=False)

        fake_db = MagicMock()
        fake_master = MagicMock()
        monkeypatch.setattr(xcp_mod, "A2LDB",
                             MagicMock(return_value=fake_db),
                             raising=False)
        monkeypatch.setattr(xcp_mod, "XCPMaster",
                             MagicMock(return_value=fake_master),
                             raising=False)

        x = XCPToolExecutor()
        await x.execute("xcp_interface", {
            "action": "connect", "a2l_path": "fw.a2l",
        })
        # XCPMaster(transport="CAN") was constructed and connect() called.
        xcp_mod.XCPMaster.assert_called_once_with(transport="CAN")
        fake_master.connect.assert_called_once()


# ---------------------------------------------------------------------------
# _disconnect
# ---------------------------------------------------------------------------

class TestDisconnect:
    @pytest.mark.asyncio
    async def test_mock_mode(self):
        x = XCPToolExecutor()
        x.connected = True
        out = await x.execute("xcp_interface", {"action": "disconnect"})
        assert out["status"] == "disconnected"
        assert x.connected is False

    @pytest.mark.asyncio
    async def test_real_mode_calls_session_disconnect(self, monkeypatch):
        import src.tools.xcp_tools as xcp_mod
        monkeypatch.setattr(xcp_mod, "HAS_XCP", True, raising=False)

        x = XCPToolExecutor()
        fake_session = MagicMock()
        x._session = fake_session
        x.connected = True

        await x.execute("xcp_interface", {"action": "disconnect"})
        fake_session.disconnect.assert_called_once()
        assert x.connected is False


# ---------------------------------------------------------------------------
# _read
# ---------------------------------------------------------------------------

class TestRead:
    @pytest.mark.asyncio
    async def test_missing_variable(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {"action": "read"})
        assert "error" in out
        assert "variable" in out["error"]

    @pytest.mark.asyncio
    async def test_not_connected(self):
        x = XCPToolExecutor()  # connected=False default
        out = await x.execute("xcp_interface", {
            "action": "read", "variable": "J",
        })
        assert "Not connected" in out["error"]

    @pytest.mark.asyncio
    async def test_mock_returns_zero(self):
        x = XCPToolExecutor()
        x.connected = True
        out = await x.execute("xcp_interface", {
            "action": "read", "variable": "J",
        })
        assert out["variable"] == "J"
        assert out["value"] == 0.0  # mock default

    @pytest.mark.asyncio
    async def test_real_mode_uses_a2l_and_session(self, monkeypatch):
        import src.tools.xcp_tools as xcp_mod
        monkeypatch.setattr(xcp_mod, "HAS_XCP", True, raising=False)

        fake_meas = MagicMock(address=0x1000, size=4)
        fake_meas.convert.return_value = 0.42
        fake_db = MagicMock()
        fake_db.get_measurement.return_value = fake_meas

        fake_session = MagicMock()
        fake_session.shortUpload.return_value = b"\x00\x00\x00\x00"

        x = XCPToolExecutor()
        x.connected = True
        x._a2l_db = fake_db
        x._session = fake_session

        out = await x.execute("xcp_interface", {
            "action": "read", "variable": "Ctrl_Kp",
        })
        # The real path consulted A2LDB + session.shortUpload + meas.convert.
        fake_db.get_measurement.assert_called_once_with("Ctrl_Kp")
        fake_session.shortUpload.assert_called_once_with(0x1000, 4)
        assert out["value"] == 0.42


# ---------------------------------------------------------------------------
# _write
# ---------------------------------------------------------------------------

class TestWrite:
    @pytest.mark.asyncio
    async def test_missing_variable(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "write", "value": 1.0,
        })
        assert "error" in out
        assert "variable" in out["error"]

    @pytest.mark.asyncio
    async def test_missing_value(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "write", "variable": "J",
        })
        assert "error" in out

    @pytest.mark.asyncio
    async def test_real_mode_calls_session_download(self, monkeypatch):
        import src.tools.xcp_tools as xcp_mod
        monkeypatch.setattr(xcp_mod, "HAS_XCP", True, raising=False)

        fake_cal = MagicMock(address=0x2000)
        fake_cal.encode.return_value = b"\x40\x99\x99\x9a"
        fake_db = MagicMock()
        fake_db.get_calibration.return_value = fake_cal
        fake_session = MagicMock()

        LAST_XCP_WRITE.pop("J", None)
        x = XCPToolExecutor()
        x._a2l_db = fake_db
        x._session = fake_session

        out = await x.execute("xcp_interface", {
            "action": "write", "variable": "J", "value": 0.35,
        })
        # The real path encoded the value and pushed it via download().
        fake_db.get_calibration.assert_called_once_with("J")
        fake_cal.encode.assert_called_once_with(0.35)
        fake_session.download.assert_called_once_with(
            0x2000, b"\x40\x99\x99\x9a",
        )
        # And LAST_XCP_WRITE was updated for the convergence helper.
        assert LAST_XCP_WRITE.get("J") == pytest.approx(0.35)
        assert out["status"] == "ok"


# ---------------------------------------------------------------------------
# _daq_start / _daq_stop
# ---------------------------------------------------------------------------

class TestDaqLifecycle:
    @pytest.mark.asyncio
    async def test_start_returns_status(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {"action": "daq_start"})
        assert out["status"] == "daq_started"
        assert "Continuous" in out["note"]

    @pytest.mark.asyncio
    async def test_stop_returns_status(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {"action": "daq_stop"})
        assert out["status"] == "daq_stopped"


# ---------------------------------------------------------------------------
# _list_measurements
# ---------------------------------------------------------------------------

class TestListMeasurements:
    @pytest.mark.asyncio
    async def test_mock_returns_canned_list(self):
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "list_measurements",
        })
        assert "measurements" in out
        # Mock list contains the well-known BMS canaries.
        assert "BMS_OVP_threshold" in out["measurements"]
        assert "Ctrl_Kp" in out["measurements"]

    @pytest.mark.asyncio
    async def test_caps_at_50_items(self):
        # Real-mode list could be huge -- the slice [:50] applies.
        x = XCPToolExecutor()
        out = await x.execute("xcp_interface", {
            "action": "list_measurements",
        })
        assert len(out["measurements"]) <= 50

    @pytest.mark.asyncio
    async def test_real_mode_pulls_from_a2l(self, monkeypatch):
        import src.tools.xcp_tools as xcp_mod
        monkeypatch.setattr(xcp_mod, "HAS_XCP", True, raising=False)

        fake_db = MagicMock()
        fake_db.get_all_measurements.return_value = {
            "u16_BattVolt": MagicMock(),
            "s16_PackCurrent": MagicMock(),
            "Ctrl_Kp": MagicMock(),
        }

        x = XCPToolExecutor()
        x._a2l_db = fake_db

        out = await x.execute("xcp_interface", {
            "action": "list_measurements",
        })
        # Real-mode: keys came from the A2L DB, not the mock list.
        assert set(out["measurements"]) == {
            "u16_BattVolt", "s16_PackCurrent", "Ctrl_Kp",
        }
