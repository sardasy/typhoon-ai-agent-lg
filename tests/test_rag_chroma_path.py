"""Coverage for the populated-Chroma path of ``RAGToolExecutor``.

The default ``conftest.py`` forces ``HAS_CHROMA=False`` to keep tests
deterministic. This file re-enables it under a fake ChromaDB client
so the production search path (`_search_chroma`) actually runs.

Covers:
  - happy path: 1 collection, hits returned with score conversion
  - empty collection skipped silently
  - missing collection skipped
  - domain filter applied via ``where`` kwarg
  - filter retry-without-where on legacy index that rejects the filter
  - results sorted by score descending, capped at top_k
  - score = 1 - distance (cosine), defaulting to 1.0 when dist absent
  - tool_name != rag_query rejected
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.tools.rag_tools import RAGToolExecutor


# ---------------------------------------------------------------------------
# Fake Chroma client + collection -- minimal surface RAGToolExecutor uses
# ---------------------------------------------------------------------------

class _FakeCollection:
    """A stand-in for chromadb.api.models.Collection."""

    def __init__(self, name: str, hits: dict | None = None,
                  count: int = 1, raise_on_filter: bool = False) -> None:
        self.name = name
        self._hits = hits
        self._count = count
        self._raise_on_filter = raise_on_filter
        self.last_kwargs: dict | None = None

    def count(self) -> int:
        return self._count

    def query(self, **kwargs):
        self.last_kwargs = dict(kwargs)
        if self._raise_on_filter and "where" in kwargs:
            raise RuntimeError(
                "where filter not supported by this index version",
            )
        return self._hits


class _FakeClient:
    def __init__(self, collections: dict[str, _FakeCollection]) -> None:
        self._collections = collections

    def list_collections(self):
        return [MagicMock(name=name) for name in self._collections]

    def get_collection(self, name: str) -> _FakeCollection:
        return self._collections[name]


def _patch_chroma(
    monkeypatch, *, collections: dict[str, _FakeCollection],
):
    """Re-enable the Chroma branch in rag_tools and inject a fake client."""
    import src.tools.rag_tools as rag_mod
    monkeypatch.setattr(rag_mod, "HAS_CHROMA", True, raising=False)
    # ``_CHROMA_DIR`` is a Path object whose ``.exists()`` is read-only;
    # replace the whole attribute with a stub whose exists() returns True.
    fake_dir = MagicMock()
    fake_dir.exists.return_value = True
    fake_dir.__str__ = lambda self: "fake_chroma_db"
    monkeypatch.setattr(rag_mod, "_CHROMA_DIR", fake_dir, raising=False)

    fake_client = _FakeClient(collections)
    fake_chroma_module = MagicMock()
    fake_chroma_module.PersistentClient.return_value = fake_client
    monkeypatch.setattr(rag_mod, "chromadb", fake_chroma_module,
                         raising=False)
    # ``Settings`` is consumed by PersistentClient; passing a MagicMock
    # is fine -- the fake client ignores the kwarg.
    monkeypatch.setattr(rag_mod, "Settings", MagicMock(), raising=False)

    # list_collections returns objects with ``.name`` for set comprehension.
    def _list_with_name():
        out = []
        for n in collections:
            m = MagicMock()
            m.name = n
            out.append(m)
        return out
    fake_client.list_collections = _list_with_name
    return fake_client


# ---------------------------------------------------------------------------
# Happy path: hits returned + scored
# ---------------------------------------------------------------------------

class TestChromaHappy:
    @pytest.mark.asyncio
    async def test_returns_documents_with_scores(self, monkeypatch):
        col = _FakeCollection(
            "thaa_standards",
            hits={
                "ids":        [["doc1", "doc2"]],
                "documents":  [["IEC 62619 §7.2.1 ...", "IEEE 1547 §6.4"]],
                "metadatas":  [[{"standard": "IEC 62619"},
                                  {"standard": "IEEE 1547"}]],
                "distances":  [[0.1, 0.4]],
            },
            count=2,
        )
        _patch_chroma(monkeypatch,
                       collections={"thaa_standards": col})

        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "overvoltage",
            "sources": ["standards"],
            "top_k": 5,
        })
        # Both docs surfaced.
        ids = [r["id"] for r in out["results"]]
        assert ids == ["doc1", "doc2"]
        # Score = 1 - distance, sorted descending (closer first).
        assert out["results"][0]["score"] == pytest.approx(0.9)
        assert out["results"][1]["score"] == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_top_k_caps_returned_count(self, monkeypatch):
        # Five hits in the collection, top_k = 2 -> only 2 returned.
        col = _FakeCollection(
            "thaa_standards",
            hits={
                "ids":       [["a", "b", "c", "d", "e"]],
                "documents": [["1", "2", "3", "4", "5"]],
                "metadatas": [[{}, {}, {}, {}, {}]],
                "distances": [[0.1, 0.2, 0.3, 0.4, 0.5]],
            },
            count=5,
        )
        _patch_chroma(monkeypatch, collections={"thaa_standards": col})

        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "x", "sources": ["standards"], "top_k": 2,
        })
        assert len(out["results"]) == 2
        # Sorted by score desc -- a (0.9) and b (0.8) win.
        assert [r["id"] for r in out["results"]] == ["a", "b"]


# ---------------------------------------------------------------------------
# Empty / missing collections
# ---------------------------------------------------------------------------

class TestChromaEmpty:
    @pytest.mark.asyncio
    async def test_zero_count_collection_skipped(self, monkeypatch):
        empty = _FakeCollection("thaa_standards", hits=None, count=0)
        _patch_chroma(monkeypatch, collections={"thaa_standards": empty})

        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "x", "sources": ["standards"],
        })
        assert out["results"] == []

    @pytest.mark.asyncio
    async def test_missing_collection_skipped(self, monkeypatch):
        # No collections registered -- query for a source whose
        # collection doesn't exist.
        _patch_chroma(monkeypatch, collections={})

        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "x", "sources": ["standards", "api_docs"],
        })
        assert out["results"] == []


# ---------------------------------------------------------------------------
# Domain filter via Chroma `where`
# ---------------------------------------------------------------------------

class TestChromaDomainFilter:
    @pytest.mark.asyncio
    async def test_where_kwarg_applied_when_domain_set(self, monkeypatch):
        col = _FakeCollection(
            "thaa_standards",
            hits={
                "ids":       [["d1"]],
                "documents": [["bms doc"]],
                "metadatas": [[{"domain": "bms"}]],
                "distances": [[0.1]],
            },
            count=1,
        )
        _patch_chroma(monkeypatch, collections={"thaa_standards": col})

        rag = RAGToolExecutor()
        await rag.execute("rag_query", {
            "query": "x", "sources": ["standards"], "domain": "bms",
        })
        assert col.last_kwargs is not None
        # ``where`` was passed and includes both bms + general fallback.
        where = col.last_kwargs["where"]
        assert where == {"domain": {"$in": ["bms", "general"]}}

    @pytest.mark.asyncio
    async def test_legacy_index_falls_back_without_where(self, monkeypatch):
        # Index that rejects the where filter (no domain metadata yet).
        # The retry path must fire and still return results.
        col = _FakeCollection(
            "thaa_standards",
            hits={
                "ids":       [["legacy"]],
                "documents": [["legacy doc"]],
                "metadatas": [[{}]],
                "distances": [[0.2]],
            },
            count=1,
            raise_on_filter=True,
        )
        _patch_chroma(monkeypatch, collections={"thaa_standards": col})

        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "x", "sources": ["standards"], "domain": "bms",
        })
        # The retry call (without ``where``) succeeded.
        assert [r["id"] for r in out["results"]] == ["legacy"]


# ---------------------------------------------------------------------------
# Score conversion edge cases
# ---------------------------------------------------------------------------

class TestScoreConversion:
    @pytest.mark.asyncio
    async def test_zero_distance_yields_score_one(self, monkeypatch):
        col = _FakeCollection(
            "thaa_standards",
            hits={
                "ids":       [["perfect"]],
                "documents": [["exact match"]],
                "metadatas": [[{}]],
                "distances": [[0.0]],
            },
            count=1,
        )
        _patch_chroma(monkeypatch, collections={"thaa_standards": col})

        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "x", "sources": ["standards"],
        })
        # Implementation: ``score = 1.0 - dist`` when dist > 0,
        # else 1.0 (treat zero distance as "no info" -> max relevance).
        assert out["results"][0]["score"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Unknown tool name
# ---------------------------------------------------------------------------

class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_returns_error(self):
        rag = RAGToolExecutor()
        out = await rag.execute("unknown_tool", {})
        assert "error" in out
        assert "Unknown RAG tool" in out["error"]


# ---------------------------------------------------------------------------
# Chroma exception -> falls back to mock
# ---------------------------------------------------------------------------

class TestChromaCrashFallback:
    @pytest.mark.asyncio
    async def test_persistent_client_init_error_falls_back(
        self, monkeypatch,
    ):
        """If ``chromadb.PersistentClient`` raises, ``execute()`` must
        log the warning and fall through to the mock backend."""
        import src.tools.rag_tools as rag_mod
        monkeypatch.setattr(rag_mod, "HAS_CHROMA", True, raising=False)
        fake_dir = MagicMock()
        fake_dir.exists.return_value = True
        monkeypatch.setattr(rag_mod, "_CHROMA_DIR", fake_dir, raising=False)
        crashing_module = MagicMock()
        crashing_module.PersistentClient.side_effect = RuntimeError(
            "chroma db corrupted",
        )
        monkeypatch.setattr(rag_mod, "chromadb", crashing_module,
                             raising=False)

        rag = RAGToolExecutor()
        out = await rag.execute("rag_query", {
            "query": "voltage", "sources": ["standards"],
        })
        # Mock KB returned -- has the IEC 62619 sample doc.
        ids = [r["id"] for r in out["results"]]
        assert any(i.startswith("iec62619") for i in ids)
