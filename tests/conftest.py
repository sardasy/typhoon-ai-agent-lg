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

import os
from pathlib import Path

import pytest


# Hard Rule 3.7 -- enforce no banned terms in test names / IDs.
# Mirim Syscon CLAUDE.md prohibits "InterBattery" mentions.
_BANNED_TERMS = ("interbattery",)


def pytest_configure(config: pytest.Config) -> None:
    """Hard Rule 3.3 -- declare the marker registry up front.

    Prevents the ``PytestUnknownMarkWarning`` and gives a single
    place to extend marker semantics.
    """
    for marker, doc in (
        ("vhil_only", "VHIL backend only (skipped on real HIL/XCP)"),
        ("hw_required", "Real ECU / HIL hardware required (skipped on mock)"),
        ("fault_injection", "Fault injection scenario (Roadmap P1)"),
        ("regression", "CI regression suite -- run on every PR"),
        ("comm_protocol", "Modbus / CAN / IEC 61850 (Roadmap P2)"),
        ("hil_measurement", "SignalAnalyzer measurement test (Roadmap P3)"),
    ):
        config.addinivalue_line("markers", f"{marker}: {doc}")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    """Hard Rule 3.7 enforcement + marker filtering.

    - Fail collection if any test ID contains a banned term.
    - Skip ``hw_required`` tests when ``DUT_MODE`` is unset / vhil.
    - Skip ``vhil_only`` tests when ``DUT_MODE=xcp``.
    """
    for item in items:
        nodeid_low = item.nodeid.lower()
        for term in _BANNED_TERMS:
            if term in nodeid_low:
                pytest.exit(f"Hard Rule 3.7 violation: {item.nodeid} "
                             f"contains banned term '{term}'", returncode=2)

    dut_mode = (os.environ.get("DUT_MODE", "vhil") or "vhil").lower()
    skip_hw = pytest.mark.skip(
        reason="hw_required marker -- set DUT_MODE=xcp to run",
    )
    skip_vhil = pytest.mark.skip(
        reason="vhil_only marker -- DUT_MODE=xcp active",
    )
    for item in items:
        if "hw_required" in item.keywords and dut_mode != "xcp":
            item.add_marker(skip_hw)
        if "vhil_only" in item.keywords and dut_mode == "xcp":
            item.add_marker(skip_vhil)


@pytest.fixture(scope="session")
def model_path(pytestconfig: pytest.Config) -> Path:
    """Hard Rule 3.3 -- absolute path to the MODEL relative to rootdir.

    Default: ``models/<env DUT_MODEL>.tse`` or
    ``models/boost.tse``. Tests may override via the ``MODEL_PATH`` env
    var (full path) for ad-hoc model files outside ``models/``.
    """
    override = os.environ.get("MODEL_PATH")
    if override:
        return Path(override).expanduser().resolve()
    name = os.environ.get("DUT_MODEL", "boost")
    return Path(pytestconfig.rootpath) / "models" / f"{name}.tse"


@pytest.fixture(scope="session")
def dut_mode() -> str:
    """Hard Rule -- single source of truth for the DUT path.

    Returns ``"vhil"`` (default) or ``"xcp"``. Tests may branch on it
    to skip hardware-only assertions while keeping the rest of the
    test body identical -- the V-model dual-path principle.
    """
    return (os.environ.get("DUT_MODE", "vhil") or "vhil").lower()


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch, tmp_path):
    """Clear module-level singletons after every test."""
    # Pin RAG tool to mock backend so tests pass regardless of whether
    # the dev machine has a populated chroma_db/.
    import src.tools.rag_tools as _rag_mod
    monkeypatch.setattr(_rag_mod, "HAS_CHROMA", False, raising=False)
    monkeypatch.setattr(_rag_mod, "HAS_QDRANT", False, raising=False)

    # P1 #11: pin audit rotation off so tests can read the configured
    # path directly. Individual tests can re-enable.
    monkeypatch.setenv("THAA_AUDIT_ROTATE", "off")
    # P0 #4: route diagnosis cache + cap to a per-test temp file so
    # one test's recorded diagnoses never leak into another's run.
    monkeypatch.setenv(
        "THAA_DIAGNOSIS_CACHE_PATH", str(tmp_path / "diag_cache.jsonl"),
    )
    monkeypatch.setenv("THAA_MAX_CLAUDE_CALLS_PER_RUN", "10000")

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

    try:
        from src.liveness import reset as _reset_liveness
        _reset_liveness()
    except ImportError:
        pass

    try:
        from src.cost_guard import reset_call_count
        reset_call_count()
    except ImportError:
        pass
