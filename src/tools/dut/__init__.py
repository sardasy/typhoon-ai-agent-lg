"""DUT (Device Under Test) abstraction layer.

Phase 4 MVP: a backend-neutral interface that lets execute_scenario and
apply_fix work against either Typhoon HIL, a real ECU via pyXCP, a
hybrid of the two, or a mock for tests, without changing scenario YAML.

Public API:
    DUTBackend          -- runtime-checkable Protocol describing a DUT
    BaseBackend         -- ABC with a default execute() shim
    HILBackend          -- wraps HILToolExecutor (current behavior)
    XCPBackend          -- wraps XCPToolExecutor (real ECU calibration only)
    HybridBackend       -- HIL stimulus + capture, XCP calibration
    MockBackend         -- deterministic in-memory backend for tests
    get_backend(name, config) -> DUTBackend   -- factory
"""

from __future__ import annotations

from typing import Any

from .base import BaseBackend, DUTBackend
from .hil_backend import HILBackend
from .hybrid_backend import HybridBackend
from .mock_backend import MockBackend
from .xcp_backend import XCPBackend


_BACKEND_REGISTRY: dict[str, type[BaseBackend]] = {
    "hil": HILBackend,
    "xcp": XCPBackend,
    "hybrid": HybridBackend,
    "mock": MockBackend,
}


def get_backend(name: str, config: dict[str, Any] | None = None) -> BaseBackend:
    """Construct a backend by short name. Unknown names fall back to "hil"."""
    cls = _BACKEND_REGISTRY.get(name) or _BACKEND_REGISTRY["hil"]
    return cls(config=config or {})


__all__ = [
    "DUTBackend",
    "BaseBackend",
    "HILBackend",
    "XCPBackend",
    "HybridBackend",
    "MockBackend",
    "get_backend",
]
