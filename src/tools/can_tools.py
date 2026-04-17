"""
CAN tools — DBC auto-loader.

Parses a DBC file and returns a structured config list suitable for driving
Typhoon HIL CAN Bus Send/Receive components (matches the signals schema in
`ai_agent_automation_plan.md` Ch 13). This module is data-only: it does not
wire components into a .tse model (SchematicAPI integration is Agent-1 work).

When `cantools` is installed it is used for canonical DBC parsing. Otherwise
a minimal regex-based parser extracts BO_/SG_ blocks so tests and the
planning path work in pure-Python environments.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import cantools  # type: ignore
    HAS_CANTOOLS = True
except ImportError:
    HAS_CANTOOLS = False
    logger.info("cantools not installed — CAN DBC loader using regex fallback")


# ---------------------------------------------------------------------------
# Tool schema (for Claude tool_use)
# ---------------------------------------------------------------------------

CAN_TOOLS: list[dict] = [
    {
        "name": "can_configure_from_dbc",
        "description": (
            "Parse a DBC file and return a structured config for CAN Bus "
            "Send/Receive components. Use this to auto-wire CAN messaging from "
            "an existing DBC definition instead of hand-defining each signal."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dbc_path": {
                    "type": "string",
                    "description": "Absolute or project-relative path to a .dbc file",
                },
                "messages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional filter — only messages whose names appear "
                        "in this list are returned. Omit to return all."
                    ),
                },
                "bus_channel": {
                    "type": "integer",
                    "description": "Target CAN bus channel number (default: 1)",
                },
            },
            "required": ["dbc_path"],
        },
    },
]


# ---------------------------------------------------------------------------
# Parser implementations
# ---------------------------------------------------------------------------

def _parse_with_cantools(dbc_path: str) -> list[dict]:
    """Full DBC parsing via cantools.database."""
    db = cantools.database.load_file(dbc_path)
    messages = []
    for msg in db.messages:
        signals = []
        for sig in msg.signals:
            signals.append({
                "name": sig.name,
                "start_bit": sig.start,
                "length": sig.length,
                "byte_order": "Little Endian" if sig.byte_order == "little_endian" else "Big Endian",
                "data_type": "int" if sig.is_signed else "uint",
                "scale": sig.scale if sig.scale is not None else 1.0,
                "offset": sig.offset if sig.offset is not None else 0.0,
                "min": sig.minimum if sig.minimum is not None else 0.0,
                "max": sig.maximum if sig.maximum is not None else 0.0,
                "unit": sig.unit or "",
            })
        messages.append({
            "name": msg.name,
            "id": msg.frame_id,
            "dlc": msg.length,
            "cycle_time_ms": msg.cycle_time or 0,
            "signals": signals,
        })
    return messages


_RE_BO = re.compile(
    r"^BO_\s+(?P<id>\d+)\s+(?P<name>\w+)\s*:\s*(?P<dlc>\d+)\s+\w+",
    re.MULTILINE,
)
_RE_SG = re.compile(
    r"^\s*SG_\s+(?P<name>\w+)\s*:\s*"
    r"(?P<start>\d+)\|(?P<length>\d+)@(?P<order>[01])(?P<sign>[+-])\s+"
    r"\((?P<scale>[-\d.eE+]+),(?P<offset>[-\d.eE+]+)\)\s+"
    r"\[(?P<min>[-\d.eE+]+)\|(?P<max>[-\d.eE+]+)\]\s+"
    r'"(?P<unit>[^"]*)"',
    re.MULTILINE,
)


def _parse_with_regex(dbc_path: str) -> list[dict]:
    """Minimal DBC parser covering BO_/SG_ blocks only. Fallback for envs
    without cantools installed. Sufficient for tests and planner scaffolding.
    """
    text = Path(dbc_path).read_text(encoding="utf-8", errors="replace")

    messages: list[dict] = []
    bo_matches = list(_RE_BO.finditer(text))

    for idx, bo in enumerate(bo_matches):
        msg_start = bo.end()
        msg_end = bo_matches[idx + 1].start() if idx + 1 < len(bo_matches) else len(text)
        block = text[msg_start:msg_end]

        signals = []
        for sg in _RE_SG.finditer(block):
            signals.append({
                "name": sg.group("name"),
                "start_bit": int(sg.group("start")),
                "length": int(sg.group("length")),
                "byte_order": "Little Endian" if sg.group("order") == "1" else "Big Endian",
                "data_type": "int" if sg.group("sign") == "-" else "uint",
                "scale": float(sg.group("scale")),
                "offset": float(sg.group("offset")),
                "min": float(sg.group("min")),
                "max": float(sg.group("max")),
                "unit": sg.group("unit"),
            })

        messages.append({
            "name": bo.group("name"),
            "id": int(bo.group("id")),
            "dlc": int(bo.group("dlc")),
            "cycle_time_ms": 0,
            "signals": signals,
        })

    return messages


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class CANToolExecutor:
    """Dispatch handler for CAN_TOOLS.

    Mirrors HILToolExecutor.execute() shape so it slots into the tool layer.
    """

    async def execute(self, tool_name: str, tool_input: dict) -> dict[str, Any]:
        if tool_name != "can_configure_from_dbc":
            return {"error": f"Unknown CAN tool: {tool_name}"}

        dbc_path = tool_input.get("dbc_path")
        if not dbc_path:
            return {"error": "dbc_path required"}

        path = Path(dbc_path)
        if not path.exists():
            return {"error": f"DBC file not found: {dbc_path}"}

        message_filter: list[str] | None = tool_input.get("messages")
        bus_channel: int = tool_input.get("bus_channel", 1)

        try:
            if HAS_CANTOOLS:
                messages = _parse_with_cantools(str(path))
                parser_used = "cantools"
            else:
                messages = _parse_with_regex(str(path))
                parser_used = "regex_fallback"
        except Exception as e:  # parser errors should not crash the graph
            logger.exception("DBC parse failure")
            return {"error": f"DBC parse error: {e}"}

        if message_filter:
            wanted = set(message_filter)
            messages = [m for m in messages if m["name"] in wanted]

        return {
            "parser": parser_used,
            "dbc_path": str(path),
            "bus_channel": bus_channel,
            "message_count": len(messages),
            "messages": messages,
        }
