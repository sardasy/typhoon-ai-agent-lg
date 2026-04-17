"""
Unit tests for the CAN DBC auto-loader.

Tests target the regex fallback path (HAS_CANTOOLS=False) so they run in
environments without the cantools package installed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tools.can_tools import (
    CAN_TOOLS,
    CANToolExecutor,
    _parse_with_regex,
)


MINIMAL_DBC = """VERSION ""

NS_ :

BS_:

BU_: BMS_ECU

BO_ 256 BMS_Status: 4 BMS_ECU
 SG_ voltage_dc : 0|16@1+ (0.1,0) [0|6553.5] "V"  Vector__XXX
 SG_ current_dc : 16|16@1- (0.01,0) [-327.68|327.67] "A"  Vector__XXX

BO_ 512 BMS_Cell_Voltages: 8 BMS_ECU
 SG_ cell_1_mv : 0|16@1+ (1,0) [0|5000] "mV"  Vector__XXX
 SG_ cell_2_mv : 16|16@1+ (1,0) [0|5000] "mV"  Vector__XXX
"""


@pytest.fixture
def dbc_file(tmp_path):
    path = tmp_path / "minimal.dbc"
    path.write_text(MINIMAL_DBC, encoding="utf-8")
    return path


class TestToolSchema:
    def test_tool_registered(self):
        names = {t["name"] for t in CAN_TOOLS}
        assert "can_configure_from_dbc" in names

    def test_dbc_path_required(self):
        tool = CAN_TOOLS[0]
        assert "dbc_path" in tool["input_schema"]["required"]


class TestRegexParser:
    def test_parses_two_messages(self, dbc_file):
        messages = _parse_with_regex(str(dbc_file))
        assert len(messages) == 2

    def test_message_fields(self, dbc_file):
        messages = _parse_with_regex(str(dbc_file))
        status = next(m for m in messages if m["name"] == "BMS_Status")
        assert status["id"] == 256
        assert status["dlc"] == 4
        assert len(status["signals"]) == 2

    def test_signal_fields(self, dbc_file):
        messages = _parse_with_regex(str(dbc_file))
        status = next(m for m in messages if m["name"] == "BMS_Status")
        voltage = next(s for s in status["signals"] if s["name"] == "voltage_dc")
        assert voltage["start_bit"] == 0
        assert voltage["length"] == 16
        assert voltage["byte_order"] == "Little Endian"
        assert voltage["data_type"] == "uint"
        assert voltage["scale"] == 0.1
        assert voltage["unit"] == "V"

        current = next(s for s in status["signals"] if s["name"] == "current_dc")
        assert current["data_type"] == "int"
        assert current["min"] == -327.68


class TestExecutor:
    async def test_missing_file_returns_error(self):
        exe = CANToolExecutor()
        result = await exe.execute("can_configure_from_dbc", {
            "dbc_path": "/nonexistent/path.dbc",
        })
        assert "error" in result

    async def test_unknown_tool_returns_error(self):
        exe = CANToolExecutor()
        result = await exe.execute("can_something_else", {})
        assert "Unknown CAN tool" in result["error"]

    async def test_missing_dbc_path(self):
        exe = CANToolExecutor()
        result = await exe.execute("can_configure_from_dbc", {})
        assert result["error"] == "dbc_path required"

    async def test_forces_regex_fallback(self, dbc_file):
        exe = CANToolExecutor()
        with patch("src.tools.can_tools.HAS_CANTOOLS", False):
            result = await exe.execute("can_configure_from_dbc", {
                "dbc_path": str(dbc_file),
                "bus_channel": 2,
            })
        assert result["parser"] == "regex_fallback"
        assert result["message_count"] == 2
        assert result["bus_channel"] == 2

    async def test_message_filter(self, dbc_file):
        exe = CANToolExecutor()
        with patch("src.tools.can_tools.HAS_CANTOOLS", False):
            result = await exe.execute("can_configure_from_dbc", {
                "dbc_path": str(dbc_file),
                "messages": ["BMS_Cell_Voltages"],
            })
        assert result["message_count"] == 1
        assert result["messages"][0]["name"] == "BMS_Cell_Voltages"


class TestAllToolsRegistration:
    def test_can_tools_in_all_tools(self):
        from src.tools import ALL_TOOLS
        names = {t["name"] for t in ALL_TOOLS}
        assert "can_configure_from_dbc" in names
