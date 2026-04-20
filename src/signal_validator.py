"""
Scenario signal-name pre-check.

Before any scenario runs, verify that every signal it references actually
exists in the loaded HIL model. A typo in scenarios_vsm_gfm.yaml used to
cost ~30 s per scenario because the agent would apply stimulus, wait for
capture, then fail silently or throw mid-execution. With pre-check, bad
signal names surface immediately -- attached to the scenario dict as
``validation_errors`` so ``execute_scenario`` can skip the run and mark
the result ``error`` with a clear reason.

Where signals come from in a scenario
-------------------------------------
- ``measurements: [...]``                         (required captures)
- ``parameters.signal`` (scalar)                  (fault template stimulus)
- ``parameters.signal_ac_sources: [...]``         (3-phase stimulus)
- ``parameters.target_sensor``                    (sensor-disconnect tests)
- ``parameters.scada_input`` / ``scada_inputs``   (ESS protection SCADA triggers)

Pseudo-signals (those that start with `$`, placeholder tokens, and
obviously-derived names like ``{target_cell}`` templates) are skipped.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Tokens that are not real signal names (resolved later by stimulus code)
_PLACEHOLDER_RE = re.compile(r"[{}$]")


def _is_placeholder(name: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(name))


def required_signals(scenario: dict) -> set[str]:
    """Collect every signal name this scenario needs from the model."""
    out: set[str] = set()
    params = scenario.get("parameters", {}) or {}

    # Measurements list (capture targets)
    for m in scenario.get("measurements", []) or []:
        if m and not _is_placeholder(m):
            out.add(m)

    # Fault template stimulus targets
    sig = params.get("signal")
    if isinstance(sig, str) and sig and not _is_placeholder(sig):
        out.add(sig)
    for sig in params.get("signal_ac_sources", []) or []:
        if isinstance(sig, str) and sig and not _is_placeholder(sig):
            out.add(sig)

    # Sensor / SCADA stimuli
    for key in ("target_sensor", "scada_input", "breaker_signal"):
        v = params.get(key)
        if isinstance(v, str) and v and not _is_placeholder(v):
            out.add(v)
    for v in params.get("scada_inputs", []) or []:
        if isinstance(v, str) and v and not _is_placeholder(v):
            out.add(v)

    # Contactor sequence (ESS precharge scenarios)
    for step in params.get("contactor_sequence", []) or []:
        if isinstance(step, dict):
            s = step.get("signal")
            if isinstance(s, str) and s and not _is_placeholder(s):
                out.add(s)

    return out


def validate_scenario(scenario: dict, model_signals: Iterable[str]) -> list[str]:
    """Return a list of human-readable validation errors for one scenario."""
    known = {s for s in model_signals if isinstance(s, str)}
    # Also accept all-SCADA inputs discovered at load time (we'd ideally
    # have that list separately, but the current discovery merges them).
    needed = required_signals(scenario)
    missing = sorted(needed - known) if known else []

    # Don't flag when model_signals hasn't been populated (e.g. mock mode
    # with empty signal discovery) -- that's a separate degraded mode.
    if not known:
        return []

    if missing:
        return [f"signal not in model: {s}" for s in missing]
    return []


def validate_all(scenarios: list[dict], model_signals: Iterable[str]) -> dict[str, list[str]]:
    """Validate every scenario. Returns {scenario_id: [errors]} (only failing)."""
    known = list(model_signals)
    out: dict[str, list[str]] = {}
    for scen in scenarios:
        errs = validate_scenario(scen, known)
        if errs:
            out[scen.get("scenario_id", "?")] = errs
    return out


def attach_validation(scenarios: list[dict], model_signals: Iterable[str]) -> int:
    """Annotate scenarios in-place with ``validation_errors``.

    Returns the number of scenarios that have at least one error.
    """
    known = list(model_signals)
    bad_count = 0
    for scen in scenarios:
        errs = validate_scenario(scen, known)
        if errs:
            scen["validation_errors"] = errs
            bad_count += 1
        else:
            scen.pop("validation_errors", None)
    return bad_count
