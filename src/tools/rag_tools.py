"""
RAG Tools — vector search for standards, API docs, test history.

Backend priority: ChromaDB (embedded, no server) > Qdrant > mock KB.
ChromaDB is the default; a populated `chroma_db/` directory is required.
Run `python scripts/index_knowledge.py` to (re)build the index.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import chromadb
    from chromadb.config import Settings
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False

try:
    from qdrant_client import QdrantClient
    HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

# Repo-level Chroma persistence directory
_CHROMA_DIR = Path(__file__).resolve().parents[2] / "chroma_db"

RAG_TOOLS: list[dict] = [
    {
        "name": "rag_query",
        "description": (
            "Search the knowledge base for relevant information. "
            "Sources: api_docs (Typhoon HIL API reference), "
            "standards (IEC/UL/KS requirements), "
            "test_history (past test results and failure resolutions), "
            "datasheets (device specifications). "
            "Optional `domain` filter narrows results to a domain "
            "namespace (bms / pcs / grid / general). "
            "Use for test planning, pass/fail criteria, and failure diagnosis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["api_docs", "standards", "test_history", "datasheets"],
                    },
                    "description": "Which knowledge sources to search",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results (default 5)",
                    "default": 5,
                },
                "domain": {
                    "type": "string",
                    "enum": ["bms", "pcs", "grid", "general"],
                    "description": (
                        "Phase 4-G: filter results to one domain "
                        "namespace. Searches both the named domain and "
                        "'general' (catch-all) in one query."
                    ),
                },
            },
            "required": ["query"],
        },
    },
]


@dataclass
class RAGToolExecutor:
    qdrant_url: str = "http://localhost:6333"
    collection_prefix: str = "thaa"
    _client: Any = None

    # Mock knowledge base for development. Each entry carries a
    # ``metadata.domain`` so Phase 4-G domain-filtered queries return
    # the right namespace; legacy entries without the tag fall back to
    # :func:`src.domain_classifier.infer_doc_domain`.
    _mock_kb: dict[str, list[dict]] = field(default_factory=lambda: {
        "standards": [
            {
                "id": "iec62619_7.2.1",
                "text": "IEC 62619 7.2.1: Each cell shall be individually monitored "
                        "for overvoltage. Protection shall activate within 100ms of "
                        "detection. The system shall disconnect the battery from the "
                        "load when any cell exceeds the maximum voltage.",
                "metadata": {"standard": "IEC 62619", "section": "7.2.1",
                             "domain": "bms"},
            },
            {
                "id": "iec62619_7.2.2",
                "text": "IEC 62619 7.2.2: Undervoltage protection shall prevent cell "
                        "voltage from dropping below minimum. Response within 200ms.",
                "metadata": {"standard": "IEC 62619", "section": "7.2.2",
                             "domain": "bms"},
            },
            {
                "id": "ieee1547_6.4",
                "text": "IEEE 1547 6.4: Voltage ride-through (LVRT/HVRT) requirements "
                        "for distributed energy resources. Minimum continuous operating "
                        "regions and maximum disconnection times.",
                "metadata": {"standard": "IEEE 1547", "section": "6.4",
                             "domain": "grid"},
            },
            {
                "id": "ieee2800_9",
                "text": "IEEE 2800-2022 §9: Grid-forming (GFM) inverter steady-state "
                        "requirements. Virtual inertia J, damping D, voltage droop Kv.",
                "metadata": {"standard": "IEEE 2800", "section": "9",
                             "domain": "grid"},
            },
        ],
        "api_docs": [
            {
                "id": "hil_capture",
                "text": "typhoon.test.capture.start_capture(duration, signals, "
                        "trigger_type, trigger_signal): Starts waveform capture. "
                        "get_capture_results() returns dict of signal_name -> numpy array.",
                "metadata": {"module": "typhoon.test.capture",
                             "domain": "general"},
            },
        ],
        "test_history": [
            {
                "id": "hist_001",
                "text": "2025-03-15: BMS OVP test cell 7 failed with 112ms response. "
                        "Root cause: scan interval misconfigured at 80ms instead of 20ms. "
                        "Fix: XCP write BMS_scanInterval_ch7 = 20.",
                "metadata": {"date": "2025-03-15", "result": "fixed",
                             "domain": "bms"},
            },
            {
                "id": "hist_002",
                "text": "2025-04-22: VSM GFM inverter LVRT test failed -- relay didn't "
                        "trip during 0.5pu sag. Root cause: J=0.05 too low for inertia. "
                        "Fix: XCP write J = 0.35.",
                "metadata": {"date": "2025-04-22", "result": "fixed",
                             "domain": "grid"},
            },
        ],
        "datasheets": [
            {
                "id": "ds_5sdd71b",
                "text": "5SDD 71B0200 diode module: VRRM=200V, IFAVM=6730A, "
                        "IFSM=71000A, VF=0.82V at 3000A.",
                "metadata": {"manufacturer": "HITACHI Energy",
                             "domain": "pcs"},
            },
        ],
    })

    async def execute(self, tool_name: str, tool_input: dict) -> dict[str, Any]:
        if tool_name != "rag_query":
            return {"error": f"Unknown RAG tool: {tool_name}"}

        query = tool_input["query"]
        sources = tool_input.get("sources", ["api_docs", "standards", "test_history"])
        top_k = tool_input.get("top_k", 5)
        domain = tool_input.get("domain")  # Phase 4-G: optional namespace filter

        # Priority: Chroma (embedded) > Qdrant (server) > mock
        if HAS_CHROMA and _CHROMA_DIR.exists():
            try:
                return self._search_chroma(query, sources, top_k, domain=domain)
            except Exception as exc:
                logger.warning("Chroma search failed (%s); falling back", exc)
        if HAS_QDRANT and self._client:
            return await self._search_qdrant(query, sources, top_k, domain=domain)
        return self._search_mock(query, sources, top_k, domain=domain)

    @staticmethod
    def _domain_where(domain: str | None) -> dict | None:
        """Phase 4-G: Chroma metadata-filter for domain namespacing.

        We always include ``general`` as a fallback bucket so docs that
        weren't classified into a specialty (e.g. shared API references)
        still surface for any agent.
        """
        if not domain:
            return None
        return {"domain": {"$in": [domain, "general"]}}

    def _search_chroma(self, query: str, sources: list[str], top_k: int,
                        *, domain: str | None = None) -> dict:
        """Query the embedded ChromaDB at chroma_db/ for each source."""
        client = chromadb.PersistentClient(
            path=str(_CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
        existing = {c.name for c in client.list_collections()}
        where = self._domain_where(domain)
        results = []
        for src in sources:
            collection_name = f"{self.collection_prefix}_{src}"
            if collection_name not in existing:
                continue
            col = client.get_collection(name=collection_name)
            if col.count() == 0:
                continue
            query_kwargs: dict = {
                "query_texts": [query],
                "n_results": min(top_k, col.count()),
            }
            if where is not None:
                query_kwargs["where"] = where
            try:
                hits = col.query(**query_kwargs)
            except Exception as exc:
                # If the collection has no domain metadata yet (older
                # index), retry without the filter so the run still gets
                # context. Indexer rerun fixes the namespace eventually.
                if where is not None:
                    logger.warning(
                        "Chroma domain filter failed on %s (%s); retrying "
                        "without filter", collection_name, exc,
                    )
                    try:
                        hits = col.query(
                            query_texts=[query],
                            n_results=min(top_k, col.count()),
                        )
                    except Exception as exc2:
                        logger.warning(
                            "Chroma query on %s failed: %s",
                            collection_name, exc2,
                        )
                        continue
                else:
                    logger.warning(
                        "Chroma query on %s failed: %s", collection_name, exc,
                    )
                    continue
            ids = (hits.get("ids") or [[]])[0]
            docs = (hits.get("documents") or [[]])[0]
            metas = (hits.get("metadatas") or [[]])[0]
            dists = (hits.get("distances") or [[]])[0]
            for i, (doc_id, doc, meta, dist) in enumerate(
                zip(ids, docs, metas or [{}] * len(ids), dists or [0.0] * len(ids))
            ):
                # Convert distance -> score (cosine: 1 - dist)
                score = max(0.0, 1.0 - float(dist)) if dist else 1.0
                results.append({
                    "id": doc_id,
                    "source": src,
                    "text": doc,
                    "score": score,
                    "metadata": meta or {},
                })
        results.sort(key=lambda x: x["score"], reverse=True)
        return {"query": query, "results": results[:top_k]}

    def _search_mock(self, query: str, sources: list[str], top_k: int,
                      *, domain: str | None = None) -> dict:
        from ..domain_classifier import infer_doc_domain
        results = []
        query_lower = query.lower()
        allowed = (domain, "general") if domain else None
        for src in sources:
            for doc in self._mock_kb.get(src, []):
                # Determine the doc's domain; fall back to inference for
                # legacy mock entries without an explicit tag.
                doc_domain = (doc.get("metadata") or {}).get("domain") \
                    or infer_doc_domain(doc["text"], doc.get("metadata"))
                if allowed is not None and doc_domain not in allowed:
                    continue
                if any(w in doc["text"].lower() for w in query_lower.split()):
                    results.append({
                        "id": doc["id"],
                        "source": src,
                        "text": doc["text"],
                        "score": 0.85,
                        "metadata": {**(doc.get("metadata") or {}),
                                     "domain": doc_domain},
                    })
        return {"query": query, "results": results[:top_k]}

    async def _search_qdrant(self, query: str, sources: list[str], top_k: int,
                              *, domain: str | None = None) -> dict:
        # Real Qdrant implementation with BGE-M3 embedding
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("BAAI/bge-m3")
        embedding = model.encode(query).tolist()

        # Phase 4-G: optional domain filter via Qdrant payload predicate.
        from qdrant_client.models import FieldCondition, Filter, MatchAny
        flt = None
        if domain:
            flt = Filter(must=[FieldCondition(
                key="metadata.domain",
                match=MatchAny(any=[domain, "general"]),
            )])

        results = []
        for src in sources:
            collection = f"{self.collection_prefix}_{src}"
            hits = self._client.search(
                collection_name=collection,
                query_vector=embedding,
                limit=top_k,
                query_filter=flt,
            )
            for hit in hits:
                results.append({
                    "id": hit.id,
                    "source": src,
                    "text": hit.payload.get("text", ""),
                    "score": hit.score,
                    "metadata": hit.payload.get("metadata", {}),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return {"query": query, "results": results[:top_k]}
