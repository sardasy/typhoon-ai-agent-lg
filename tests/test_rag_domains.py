"""Tests for Phase 4-G RAG domain namespacing."""

from __future__ import annotations

import pytest

from src.domain_classifier import infer_doc_domain
from src.tools.rag_tools import RAGToolExecutor


@pytest.fixture(autouse=True)
def _force_mock_kb(monkeypatch):
    """Bypass any indexed Chroma DB so these tests pin the mock KB.

    A populated ``chroma_db/`` directory in the repo would otherwise
    short-circuit ``_search_mock``; we want deterministic behavior
    here (tests of the namespace filter, not of the indexer's output).
    """
    import src.tools.rag_tools as rag_mod
    monkeypatch.setattr(rag_mod, "HAS_CHROMA", False, raising=False)
    monkeypatch.setattr(rag_mod, "HAS_QDRANT", False, raising=False)


# ---------------------------------------------------------------------------
# infer_doc_domain heuristic
# ---------------------------------------------------------------------------

class TestInferDocDomain:
    def test_explicit_metadata_wins(self):
        assert infer_doc_domain("anything", {"domain": "grid"}) == "grid"

    def test_iec_62619_metadata_is_bms(self):
        assert infer_doc_domain("...", {"standard": "IEC 62619"}) == "bms"

    def test_ieee_2800_metadata_is_grid(self):
        assert infer_doc_domain("...", {"standard": "IEEE 2800-2022"}) == "grid"

    def test_iec_61851_metadata_is_pcs(self):
        assert infer_doc_domain("...", {"standard": "IEC 61851"}) == "pcs"

    def test_text_vote_bms(self):
        text = "BMS scan interval misconfigured. Cell voltage exceeds OVP threshold."
        assert infer_doc_domain(text, {}) == "bms"

    def test_text_vote_grid(self):
        text = "GFM virtual inertia for IEEE 2800. ROCOF response on grid disturbance."
        assert infer_doc_domain(text, {}) == "grid"

    def test_text_vote_pcs(self):
        text = "PI controller Ctrl_Kp tuning. Duty cycle saturation in DC bus regulator."
        assert infer_doc_domain(text, {}) == "pcs"

    def test_unknown_falls_back_to_general(self):
        assert infer_doc_domain("hello world", {}) == "general"


# ---------------------------------------------------------------------------
# Mock-backed RAG search with domain filter
# ---------------------------------------------------------------------------

class TestMockDomainFilter:
    @pytest.mark.asyncio
    async def test_no_domain_returns_all(self):
        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "voltage", "sources": ["standards"],
        })
        ids = [r["id"] for r in out["results"]]
        # Both BMS and Grid OVP/voltage docs should appear.
        assert any(i.startswith("iec62619") for i in ids)
        assert any(i.startswith("ieee") for i in ids)

    @pytest.mark.asyncio
    async def test_bms_domain_excludes_grid(self):
        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "voltage", "sources": ["standards"],
            "domain": "bms",
        })
        for r in out["results"]:
            assert r["metadata"]["domain"] in ("bms", "general")

    @pytest.mark.asyncio
    async def test_grid_domain_excludes_bms(self):
        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "voltage", "sources": ["standards"],
            "domain": "grid",
        })
        for r in out["results"]:
            assert r["metadata"]["domain"] in ("grid", "general")

    @pytest.mark.asyncio
    async def test_pcs_domain_returns_pcs_or_general_only(self):
        # The mock KB has a PCS-tagged datasheet. Querying datasheets
        # with domain=pcs should return it; querying with domain=bms
        # should not.
        rag = RAGToolExecutor()
        out_pcs = await rag.execute("rag_query", {
            "query": "diode", "sources": ["datasheets"], "domain": "pcs",
        })
        ids_pcs = [r["id"] for r in out_pcs["results"]]
        assert "ds_5sdd71b" in ids_pcs

        out_bms = await rag.execute("rag_query", {
            "query": "diode", "sources": ["datasheets"], "domain": "bms",
        })
        ids_bms = [r["id"] for r in out_bms["results"]]
        assert "ds_5sdd71b" not in ids_bms

    @pytest.mark.asyncio
    async def test_general_namespace_falls_through_for_any_domain(self):
        # The api_docs entry is tagged "general" -- it should appear in
        # results regardless of which domain the agent specifies.
        rag = RAGToolExecutor()
        for d in ("bms", "pcs", "grid"):
            out = await rag.execute("rag_query", {
                "query": "capture", "sources": ["api_docs"], "domain": d,
            })
            ids = [r["id"] for r in out["results"]]
            assert "hil_capture" in ids, f"general doc missing for domain={d}"


# ---------------------------------------------------------------------------
# Domain-where helper
# ---------------------------------------------------------------------------

class TestDomainWhere:
    def test_none_returns_no_filter(self):
        assert RAGToolExecutor._domain_where(None) is None

    def test_explicit_domain_includes_general(self):
        w = RAGToolExecutor._domain_where("bms")
        assert w == {"domain": {"$in": ["bms", "general"]}}


# ---------------------------------------------------------------------------
# load_model populates rag_context_by_domain
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_model_populates_per_domain_rag(tmp_path, monkeypatch):
    """load_model should fetch a context bucket per domain."""
    cfg = tmp_path / "model.yaml"
    cfg.write_text("model:\n  path: dummy.tse\n", encoding="utf-8")

    from src.nodes.load_model import load_model
    state = {
        "goal": "BMS overvoltage protection",
        "config_path": str(cfg),
        "dut_backend": "mock",
        "dut_config": {},
    }
    out = await load_model(state)
    by_domain = out["rag_context_by_domain"]
    assert set(by_domain.keys()) == {"bms", "pcs", "grid", "general"}
    # Mock KB has BMS and Grid OVP entries -- BMS bucket should be non-empty.
    assert by_domain["bms"]
