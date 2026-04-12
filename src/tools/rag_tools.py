"""
RAG Tools — Qdrant vector search for standards, API docs, test history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

try:
    from qdrant_client import QdrantClient
    HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

RAG_TOOLS: list[dict] = [
    {
        "name": "rag_query",
        "description": (
            "Search the knowledge base for relevant information. "
            "Sources: api_docs (Typhoon HIL API reference), "
            "standards (IEC/UL/KS requirements), "
            "test_history (past test results and failure resolutions), "
            "datasheets (device specifications). "
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

    # Mock knowledge base for development
    _mock_kb: dict[str, list[dict]] = field(default_factory=lambda: {
        "standards": [
            {
                "id": "iec62619_7.2.1",
                "text": "IEC 62619 7.2.1: Each cell shall be individually monitored "
                        "for overvoltage. Protection shall activate within 100ms of "
                        "detection. The system shall disconnect the battery from the "
                        "load when any cell exceeds the maximum voltage.",
                "metadata": {"standard": "IEC 62619", "section": "7.2.1"},
            },
            {
                "id": "iec62619_7.2.2",
                "text": "IEC 62619 7.2.2: Undervoltage protection shall prevent cell "
                        "voltage from dropping below minimum. Response within 200ms.",
                "metadata": {"standard": "IEC 62619", "section": "7.2.2"},
            },
        ],
        "api_docs": [
            {
                "id": "hil_capture",
                "text": "typhoon.test.capture.start_capture(duration, signals, "
                        "trigger_type, trigger_signal): Starts waveform capture. "
                        "get_capture_results() returns dict of signal_name -> numpy array.",
                "metadata": {"module": "typhoon.test.capture"},
            },
        ],
        "test_history": [
            {
                "id": "hist_001",
                "text": "2025-03-15: BMS OVP test cell 7 failed with 112ms response. "
                        "Root cause: scan interval misconfigured at 80ms instead of 20ms. "
                        "Fix: XCP write BMS_scanInterval_ch7 = 20.",
                "metadata": {"date": "2025-03-15", "result": "fixed"},
            },
        ],
        "datasheets": [
            {
                "id": "ds_5sdd71b",
                "text": "5SDD 71B0200 diode module: VRRM=200V, IFAVM=6730A, "
                        "IFSM=71000A, VF=0.82V at 3000A.",
                "metadata": {"manufacturer": "HITACHI Energy"},
            },
        ],
    })

    async def execute(self, tool_name: str, tool_input: dict) -> dict[str, Any]:
        if tool_name != "rag_query":
            return {"error": f"Unknown RAG tool: {tool_name}"}

        query = tool_input["query"]
        sources = tool_input.get("sources", ["api_docs", "standards", "test_history"])
        top_k = tool_input.get("top_k", 5)

        if HAS_QDRANT and self._client:
            return await self._search_qdrant(query, sources, top_k)
        return self._search_mock(query, sources, top_k)

    def _search_mock(self, query: str, sources: list[str], top_k: int) -> dict:
        results = []
        query_lower = query.lower()
        for src in sources:
            for doc in self._mock_kb.get(src, []):
                if any(w in doc["text"].lower() for w in query_lower.split()):
                    results.append({
                        "id": doc["id"],
                        "source": src,
                        "text": doc["text"],
                        "score": 0.85,
                        "metadata": doc.get("metadata", {}),
                    })
        return {"query": query, "results": results[:top_k]}

    async def _search_qdrant(self, query: str, sources: list[str], top_k: int) -> dict:
        # Real Qdrant implementation with BGE-M3 embedding
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("BAAI/bge-m3")
        embedding = model.encode(query).tolist()

        results = []
        for src in sources:
            collection = f"{self.collection_prefix}_{src}"
            hits = self._client.search(
                collection_name=collection,
                query_vector=embedding,
                limit=top_k,
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
