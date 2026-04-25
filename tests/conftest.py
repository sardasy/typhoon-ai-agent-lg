"""Shared test fixtures and isolation guards.

THAA carries several module-level singletons that survive across tests
when not explicitly reset:

    src.nodes.load_model._dut_singletons   -- DUT backend cache
    src.tools.dut.base._DEVICE_LOCKS       -- per-device asyncio locks
    src.tools.xcp_tools.LAST_XCP_WRITE     -- mock XCP write tracker
    src.twin._twin                         -- DigitalTwin singleton

Without isolation, test order determines pass/fail. The autouse fixture
below clears these after every test. Tests that intentionally seed any
of them do so before the assertion -- the cleanup runs *after* yield.

Also pins ``HAS_CHROMA = False`` for the RAG tool by default. The repo
ships an empty ``chroma_db/`` directory, which causes
``RAGToolExecutor._search_chroma`` to short-circuit on
``col.count() == 0`` -- the mock-KB fallback never fires, and tests that
expect mock results (e.g. test_graph.py::TestRAGToolsMock::test_query)
fail. Tests that need the real Chroma path can re-enable it locally
with ``monkeypatch.setattr(rag_mod, "HAS_CHROMA", True)``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    """Clear module-level singletons after every test."""
    # Pin RAG tool to mock backend so tests pass regardless of whether
    # the dev machine has a populated chroma_db/.
    import src.tools.rag_tools as _rag_mod
    monkeypatch.setattr(_rag_mod, "HAS_CHROMA", False, raising=False)
    monkeypatch.setattr(_rag_mod, "HAS_QDRANT", False, raising=False)

    yield

    # Drop accumulated state so the next test gets a clean slate.
    try:
        from src.nodes.load_model import _dut_singletons
        _dut_singletons.clear()
    except ImportError:
        pass

    try:
        from src.tools.dut.base import _DEVICE_LOCKS
        _DEVICE_LOCKS.clear()
    except ImportError:
        pass

    try:
        from src.tools.xcp_tools import LAST_XCP_WRITE
        LAST_XCP_WRITE.clear()
    except ImportError:
        pass

    try:
        from src.twin import reset_twin
        reset_twin()
    except ImportError:
        pass
