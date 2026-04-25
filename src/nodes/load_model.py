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
from ..tools.dut import BaseBackend, HILBackend, get_backend
from ..tools.hil_tools import HAS_TYPHOON, HILToolExecutor
from ..tools.rag_tools import RAGToolExecutor


# Module-level singletons (shared across graph invocations).
# DUT backends are cached by ``(name, frozenset(config.items()))`` so distinct
# configurations (e.g. different a2l paths) get distinct instances while the
# common hil/no-config case is shared with the legacy ``_hil`` singleton.
_dut_singletons: dict[tuple[str, "frozenset[Any]"], BaseBackend] = {}
_rag = RAGToolExecutor()


def _legacy_hil_backend() -> HILBackend:
    """Return the cached default HIL backend, instantiating once."""
    key: tuple[str, frozenset[Any]] = ("hil", frozenset())
    backend = _dut_singletons.get(key)
    if backend is None:
        backend = HILBackend(config={})
        _dut_singletons[key] = backend
    return backend  # type: ignore[return-value]


def get_hil() -> HILToolExecutor:
    """Backward-compat accessor: returns the underlying HILToolExecutor.

    Existing callers (tests, fault_templates indirectly) keep working.
    New code should call :func:`get_dut` instead.
    """
    return _legacy_hil_backend().hil


def get_dut(
    state: AgentState | dict | None = None,
    *,
    scenario: dict | None = None,
) -> BaseBackend:
    """Return the DUT backend for the current state.

    Backends are singletons keyed by (name, device_id, config). The
    default is ``HILBackend`` on the ``"default"`` device -- preserves
    current behavior when ``dut_backend`` and ``device_id`` are unset.

    Phase 4-I: when ``scenario`` is supplied and carries
    ``scenario["device_id"]``, the matching overlay from
    ``state["device_pool"]`` is merged on top of ``state["dut_config"]``
    so the backend instance points at the right physical device.
    """
    state = state or {}
    name = state.get("dut_backend") or "hil"
    base_config = dict(state.get("dut_config") or {})

    # Phase 4-I device routing.
    pool = state.get("device_pool") or {}
    device_id = "default"
    if scenario is not None:
        device_id = scenario.get("device_id") or "default"
    elif "device_id" in base_config:
        device_id = base_config["device_id"]

    overlay = pool.get(device_id) or {}
    config = {**base_config, **overlay, "device_id": device_id}

    try:
        config_key = frozenset((k, _hashable(v)) for k, v in config.items())
    except TypeError:
        config_key = frozenset()  # unhashable -- give up on caching
    key = (name, config_key)
    backend = _dut_singletons.get(key)
    if backend is None:
        backend = get_backend(name, config)
        _dut_singletons[key] = backend
    return backend


def _hashable(value):
    if isinstance(value, (list, tuple)):
        return tuple(_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in value.items()))
    return value


def get_rag() -> RAGToolExecutor:
    return _rag


async def load_model(state: AgentState) -> dict[str, Any]:
    """Load HIL model, discover signals, fetch RAG context."""

    config_path = state.get("config_path", "configs/model.yaml")
    cfg: dict[str, Any] = {}
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
                f"Unknown preset '{preset_name}' — ignoring",
            ))

    # Load model via the configured DUT backend (default: HIL).
    dut = get_dut(state)
    result = await dut.control("load", model_path=model_path)
    signals = result.get("signals", [])

    # Start simulation (no-op for XCP-only backends).
    await dut.control("start")

    # RAG query for standards context. Phase 4-G: also fetch per-domain
    # namespaces so the orchestrator's BMS/PCS/Grid agents see only
    # context relevant to their work.
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

    # Per-domain pull (Phase 4-G). ``ALL_DOMAINS`` is small (4 names),
    # so this adds 4 tiny calls during init -- well worth the
    # signal-to-noise improvement at analyzer time.
    from ..domain_classifier import ALL_DOMAINS
    rag_context_by_domain: dict[str, str] = {}
    for d in ALL_DOMAINS:
        sub = await rag.execute("rag_query", {
            "query": goal,
            "sources": ["standards", "test_history"],
            "top_k": 5,
            "domain": d,
        })
        rag_context_by_domain[d] = "\n".join(
            r["text"] for r in sub.get("results", [])
        )

    nonempty = sum(1 for v in rag_context_by_domain.values() if v)
    events.append(make_event(
        "load_model", "observation",
        f"Model loaded: {len(signals)} signals. RAG: "
        f"{len(rag_result.get('results', []))} docs (global) + "
        f"{nonempty}/{len(ALL_DOMAINS)} domain namespaces populated.",
        {"domain_namespaces": {d: bool(v) for d, v in rag_context_by_domain.items()}},
    ))

    return {
        "model_path": model_path,
        "model_signals": signals,
        "model_loaded": True,
        "device_mode": device_mode,
        "active_preset": active_preset,
        "rag_context": rag_context,
        "rag_context_by_domain": rag_context_by_domain,
        "events": events,
    }
