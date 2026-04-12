"""
Node: load_model

Loads the Typhoon HIL model, discovers signals, queries RAG for
relevant standards and test history. First node after START.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..state import AgentState, make_event
from ..tools.hil_tools import HILToolExecutor
from ..tools.rag_tools import RAGToolExecutor


# Module-level singletons (shared across graph invocations)
_hil = HILToolExecutor()
_rag = RAGToolExecutor()


def get_hil() -> HILToolExecutor:
    return _hil


def get_rag() -> RAGToolExecutor:
    return _rag


async def load_model(state: AgentState) -> dict[str, Any]:
    """Load HIL model, discover signals, fetch RAG context."""

    config_path = state.get("config_path", "configs/model.yaml")
    cfg = {}
    p = Path(config_path)
    if p.exists():
        cfg = yaml.safe_load(p.read_text()) or {}

    model_path = cfg.get("model", {}).get("path", "")

    # Load model
    hil = get_hil()
    result = await hil.execute("hil_control", {
        "action": "load",
        "model_path": model_path,
    })
    signals = result.get("signals", [])

    # Start simulation
    await hil.execute("hil_control", {"action": "start"})

    # RAG query for standards context
    goal = state.get("goal", "")
    rag = get_rag()
    rag_result = await rag.execute("rag_query", {
        "query": goal,
        "sources": ["standards", "test_history"],
        "top_k": 5,
    })
    rag_context = "\n".join(
        r["text"] for r in rag_result.get("results", [])
    )

    return {
        "model_path": model_path,
        "model_signals": signals,
        "model_loaded": True,
        "rag_context": rag_context,
        "events": [
            make_event(
                "load_model", "observation",
                f"Model loaded: {len(signals)} signals. RAG: {len(rag_result.get('results', []))} docs.",
            )
        ],
    }
