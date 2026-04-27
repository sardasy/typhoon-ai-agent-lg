"""Heuristic scenario domain classifier (Phase 4-B).

Maps a scenario dict to one of:

    "bms"      -- Battery Management System: cell V/T, SOC, OVP/UVP/OCP
    "pcs"      -- Power Conversion System: DC/DC, inverter steady-state
    "grid"     -- Grid-tied / GFM / IEEE 1547 / IEEE 2800 compliance
    "general"  -- catch-all / unclassifiable

The classifier is a pure function -- it inspects ``standard_ref``,
``category``, ``measurements``, parameter keys, and ``fault_template``
to vote. Used by ``plan_tests`` to tag every scenario, by the
orchestrator to bucket scenarios into domain agents, and by
``analyze_failure`` to overlay domain-specific guidance on the
analyzer prompt.

Known signals / templates per domain:

    BMS    -> V_cell_*, BMS_*, fault_template in {overvoltage,
              undervoltage, short_circuit, open_circuit} on cell signals,
              standard_ref starts with "IEC 62619" / "UN 38.3"
    PCS    -> Vdc, Idc, Vout, Iout, IGBT_*, PWM_*, DC-DC keywords
    Grid   -> Vgrid, V[abc], Vsa/Vsb/Vsc, Pe, Qe, w, fault_template in
              {voltage_sag, voltage_swell, frequency_deviation,
              vsm_steady_state, vsm_pref_step, phase_jump},
              standard_ref starts with "IEEE 1547" or "IEEE 2800"
"""

from __future__ import annotations

from typing import Any, Literal

Domain = Literal["bms", "pcs", "grid", "general"]
ALL_DOMAINS: tuple[Domain, ...] = ("bms", "pcs", "grid", "general")

# Display order: BMS first (battery work tends to gate later grid tests),
# then PCS (DC bus must be healthy), then grid (highest-level compliance),
# then general (anything we couldn't classify).
DOMAIN_ORDER: dict[Domain, int] = {
    "bms": 0, "pcs": 1, "grid": 2, "general": 3,
}

_BMS_TEMPLATES = {
    "overvoltage", "undervoltage", "short_circuit", "open_circuit",
}
_GRID_TEMPLATES = {
    "voltage_sag", "voltage_swell", "frequency_deviation",
    "vsm_steady_state", "vsm_pref_step", "phase_jump",
}

_BMS_SIGNAL_PREFIXES = ("V_cell_", "T_cell_", "BMS_", "SOC_", "Pack_")
_GRID_SIGNAL_TOKENS = (
    "Vgrid", "Vsa", "Vsb", "Vsc", "Vga", "Vgb", "Vgc",
    "Pe", "Qe", "Pref", "Qref", "Kv", "ROCOF",
)
_PCS_SIGNAL_TOKENS = (
    "Vdc", "Idc", "Vbus", "Ibus", "Vout", "Iout",
    "IGBT_", "PWM_", "Duty",
)


def classify(scenario: dict[str, Any]) -> Domain:
    """Return the domain label for ``scenario``.

    Pure function: never raises, never reads state outside ``scenario``.
    Heuristic precedence:

        1. Explicit ``scenario["domain"]`` if already set
        2. ``standard_ref`` prefix (IEC 62619 -> bms, IEEE 1547/2800 -> grid)
        3. ``parameters.fault_template``
        4. Signal name patterns in ``measurements`` and parameter values
        5. Otherwise "general"
    """
    explicit = scenario.get("domain")
    if isinstance(explicit, str) and explicit in ALL_DOMAINS:
        return explicit  # type: ignore[return-value]

    std = (scenario.get("standard_ref") or "").upper()
    if std.startswith("IEC 62619") or std.startswith("UN 38.3") or std.startswith("UL 1973"):
        return "bms"
    if std.startswith("IEEE 1547") or std.startswith("IEEE 2800") or std.startswith("UL 1741"):
        return "grid"
    if std.startswith("IEC 61851") or std.startswith("UL 9540"):
        # 61851 = EV charging coupler, 9540 = ESS safety -- both are PCS-side
        return "pcs"

    params = scenario.get("parameters") or {}
    template = (params.get("fault_template") or "").lower()
    if template in _BMS_TEMPLATES:
        # Cell-level fault templates are BMS only when they target a cell signal.
        target = (params.get("signal") or params.get("target_sensor") or "")
        if any(target.startswith(p) for p in _BMS_SIGNAL_PREFIXES):
            return "bms"
        # Same template names appear at AC level (overvoltage on Vgrid),
        # so fall through to signal-based vote.
    if template in _GRID_TEMPLATES:
        return "grid"

    # Vote by signals
    signals: list[str] = list(scenario.get("measurements") or [])
    for k in ("signal", "target_sensor", "scada_input", "breaker_signal"):
        v = params.get(k)
        if isinstance(v, str):
            signals.append(v)
    for k in ("signal_ac_sources", "scada_inputs"):
        v = params.get(k)
        if isinstance(v, list):
            signals.extend(s for s in v if isinstance(s, str))

    bms_hits = sum(
        any(s.startswith(p) for p in _BMS_SIGNAL_PREFIXES) for s in signals
    )
    grid_hits = sum(
        any(tok in s for tok in _GRID_SIGNAL_TOKENS) for s in signals
    )
    pcs_hits = sum(
        any(tok in s for tok in _PCS_SIGNAL_TOKENS) for s in signals
    )

    if bms_hits and bms_hits >= max(grid_hits, pcs_hits):
        return "bms"
    if grid_hits and grid_hits >= pcs_hits:
        return "grid"
    if pcs_hits:
        return "pcs"

    # Last-resort vote on category
    cat = (scenario.get("category") or "").lower()
    if "battery" in cat or "cell" in cat or "bms" in cat:
        return "bms"
    if "grid" in cat or "frt" in cat or "lvrt" in cat or "hvrt" in cat:
        return "grid"
    if "inverter" in cat or "converter" in cat or "pcs" in cat:
        return "pcs"

    return "general"


def annotate(scenarios: list[dict[str, Any]]) -> dict[Domain, int]:
    """Tag each scenario in-place with ``domain`` and return per-domain counts."""
    counts: dict[Domain, int] = {d: 0 for d in ALL_DOMAINS}
    for s in scenarios:
        d = classify(s)
        s["domain"] = d
        counts[d] += 1
    return counts


def sort_by_domain(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new list sorted by (domain, original_priority).

    Stable: scenarios within a domain keep their relative order.
    """
    return sorted(
        scenarios,
        key=lambda s: (
            DOMAIN_ORDER.get(s.get("domain", "general"), 99),
            s.get("priority", 999),
        ),
    )


# ---------------------------------------------------------------------------
# Per-domain analyzer prompt overlays
# ---------------------------------------------------------------------------

# Per-domain analyzer prompt overlays live in ``prompts/domains/<d>.md``.
# Loaded lazily on first ``overlay_for()`` call and cached in-process so
# disk I/O happens at most once per domain. The "general" domain has no
# overlay file (the base analyzer prompt is sufficient).

from pathlib import Path as _Path

_PROMPTS_DIR = _Path(__file__).resolve().parent.parent / "prompts" / "domains"
_OVERLAY_CACHE: dict[str, str] = {}


def overlay_for(domain: Domain | str) -> str:
    """Return the analyzer-prompt overlay for a domain. Empty string if none.

    Reads ``prompts/domains/<domain>.md`` once, then serves from a
    process-local cache. Missing files (the legitimate case for
    ``general``, plus any operator-deleted overlay) yield "".
    """
    if domain in _OVERLAY_CACHE:
        return _OVERLAY_CACHE[domain]
    # ``Domain`` is a Literal[str] alias, so any value here is already a
    # str at runtime -- no isinstance guard needed.
    path = _PROMPTS_DIR / f"{domain}.md"
    text = ""
    if path.is_file():
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            text = ""
    _OVERLAY_CACHE[domain] = text
    return text


def _reset_overlay_cache() -> None:
    """Test helper: drop the overlay cache so unit tests can swap files."""
    _OVERLAY_CACHE.clear()


# ---------------------------------------------------------------------------
# Document-domain inferrer (Phase 4-G RAG namespacing).
#
# Used by the indexer (``scripts/index_knowledge.py``) and by the RAG
# tool's mock KB to tag knowledge-base documents with a domain. The
# heuristic mirrors :func:`classify` -- standard prefixes vote first,
# then signal-name patterns, then category keywords. We deliberately
# DON'T return "general" eagerly: it's the catch-all when no other
# domain has hits, so query-time domain filters need to use it as the
# OR-fallback (search bms + general for a BMS scenario, etc.).
# ---------------------------------------------------------------------------

def infer_doc_domain(text: str, metadata: dict | None = None) -> Domain:
    """Infer the domain of a knowledge-base document.

    Inputs are deliberately permissive: ``text`` is the raw document
    content, ``metadata`` may carry pre-set fields like ``standard``
    or ``topic`` from the indexer. Output is one of ``ALL_DOMAINS``.
    """
    metadata = metadata or {}
    explicit = metadata.get("domain")
    if isinstance(explicit, str) and explicit in ALL_DOMAINS:
        return explicit  # type: ignore[return-value]

    std = (metadata.get("standard") or "").upper()
    if std.startswith("IEC 62619") or std.startswith("UN 38.3") or std.startswith("UL 1973"):
        return "bms"
    if std.startswith("IEEE 1547") or std.startswith("IEEE 2800") or std.startswith("UL 1741"):
        return "grid"
    if std.startswith("IEC 61851") or std.startswith("UL 9540"):
        return "pcs"

    text_l = (text or "").lower()
    bms_hits = sum(text_l.count(tok.lower()) for tok in (
        "BMS", "cell voltage", "OVP threshold", "UVP threshold",
        "scan interval", "battery management", "IEC 62619",
    ))
    grid_hits = sum(text_l.count(tok.lower()) for tok in (
        "grid", "IEEE 1547", "IEEE 2800", "GFM", "virtual inertia",
        "ROCOF", "lvrt", "hvrt", "anti-islanding",
    ))
    pcs_hits = sum(text_l.count(tok.lower()) for tok in (
        "DC bus", "duty cycle", "PI controller", "PWM",
        "Ctrl_Kp", "deadtime", "current loop",
    ))

    if bms_hits and bms_hits >= max(grid_hits, pcs_hits):
        return "bms"
    if grid_hits and grid_hits >= pcs_hits:
        return "grid"
    if pcs_hits:
        return "pcs"
    return "general"
