"""
Node: load_model

Loads the Typhoon HIL model, discovers signals, queries RAG for
relevant standards and test history. First node after START.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..presets import get_preset
from ..state import AgentState, make_event
from ..tools.hil_tools import HAS_TYPHOON, HILToolExecutor
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
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    model_cfg = cfg.get("model", {}) or {}
    model_path = model_cfg.get("path", "")

    # Explicit device-mode detection (Quick Win 4). HAS_TYPHOON is set at
    # import time in hil_tools based on whether the Typhoon API is available.
    device_mode = "typhoon" if HAS_TYPHOON else "vhil_mock"

    events = [
        make_event(
            "load_model", "observation",
            f"Device mode: {device_mode}"
            + ("" if HAS_TYPHOON else " (Typhoon HIL API unavailable, running on Virtual HIL)"),
            {"device_mode": device_mode},
        )
    ]

    # Preset merge (Quick Win 5). Explicit model.* values override preset values.
    preset_name = model_cfg.get("preset", "") or ""
    active_preset = ""
    if preset_name:
        preset = get_preset(preset_name)
        if preset:
            active_preset = preset_name
            events.append(make_event(
                "load_model", "observation",
                f"Applied model preset: {preset_name}",
                {"preset": preset_name, "values": preset},
            ))
        else:
            events.append(make_event(
                "load_model", "warning",
                f"Unknown preset '{preset_name}' -- ignoring",
            ))

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

    events.append(make_event(
        "load_model", "observation",
        f"Model loaded: {len(signals)} signals. RAG: {len(rag_result.get('results', []))} docs.",
    ))

    return {
        "model_path": model_path,
        "model_signals": signals,
        "model_loaded": True,
        "device_mode": device_mode,
        "active_preset": active_preset,
        "rag_context": rag_context,
        "events": events,
    }
