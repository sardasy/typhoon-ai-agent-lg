"""
Tool Registry — all tools available to the AI Agent via Claude tool_use.
"""

from .can_tools import CAN_TOOLS, CANToolExecutor
from .hil_tools import HIL_TOOLS, HILToolExecutor
from .rag_tools import RAG_TOOLS, RAGToolExecutor
from .xcp_tools import XCP_TOOLS, XCPToolExecutor
from .hil_api_docs import HilApiDocsExecutor, get_hil_api_docs
from .pytest_api import PytestApiExecutor, get_pytest_api, parse_ini_markers

# Combined tool definitions (sent to Claude API)
ALL_TOOLS: list[dict] = HIL_TOOLS + XCP_TOOLS + RAG_TOOLS + CAN_TOOLS

__all__ = [
    "ALL_TOOLS",
    "CANToolExecutor",
    "HILToolExecutor",
    "RAGToolExecutor",
    "XCPToolExecutor",
    "HilApiDocsExecutor",
    "get_hil_api_docs",
    "PytestApiExecutor",
    "get_pytest_api",
    "parse_ini_markers",
]
