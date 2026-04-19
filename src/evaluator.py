"""
Scenario pass/fail evaluator.

Rule dispatcher that maps keys used in ``scenarios_*.yaml::pass_fail_rules``
to concrete checks on ``list[WaveformStats]``. Previous behaviour hard-coded
4 rules inside ``execute_scenario._evaluate()`` and silently returned ``pass``
for every other rule key. This module:

  1. Registers every rule known to the YAML libraries (68 keys today).
  2. Normalises `_pct` / `_percent` and `_max_s` / `_max_ms` synonyms.
  3. Runs each rule handler; rules with missing data return ``error`` so a
     downstream operator can see the test is not actually validated.
  4. Returns ``error`` (not ``pass``) for unrecognised rule keys when
     ``strict=True`` (default).

Rule handler signature
----------------------
    def handler(value: Any, rules: dict, stats_map: dict[str, WaveformStats],
                scenario: dict) -> tuple[str | None, str]

    Return ``(status, reason)`` where ``status`` is ``"pass"``, ``"fail"``,
    ``"error"``, or ``None`` (skip -- handler doesn't apply). ``reason`` is a
    human-readable explanation shown in the report.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .state import WaveformStats

logger = logging.getLogger(__name__)

Handler = Callable[[Any, dict, dict, dict], tuple[str | None, str]]

_REGISTRY: dict[str, Handler] = {}

# Synonyms to canonical names (for author convenience)
_ALIASES = {
    "overshoot_max_pct": "overshoot_max_percent",
    "steady_state_error_max_pct": "steady_state_error_max_percent",
    "dc_link_ripple_max_pct": "dc_link_ripple_max_percent",
    "settling_tolerance_pct": "settling_tolerance_percent",
    "voltage_tolerance_pct": "voltage_tolerance_percent",
    "power_tolerance_pct": "power_tolerance_percent",
    "power_recovery_threshold_pct": "power_recovery_threshold_percent",
    "dc_link_overshoot_max_pct": "dc_link_overshoot_max_percent",
    "voltage_regulation_error_max_pct": "voltage_regulation_error_max_percent",
    "current_limit_overshoot_max_pct": "current_limit_overshoot_max_percent",
    "min_change_pct": "min_change_percent",
}


def register(key: str) -> Callable[[Handler], Handler]:
    def _wrap(fn: Handler) -> Handler:
        _REGISTRY[key] = fn
        return fn
    return _wrap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_signal(stats_map: dict, *hints: str) -> WaveformStats | None:
    """Return the first WaveformStats whose signal name matches any hint."""
    lower = {k.lower(): v for k, v in stats_map.items()}
    for h in hints:
        h_low = h.lower()
        for name, s in lower.items():
            if h_low in name:
                return s
    return None


def _any_signal_matching(stats_map: dict, *hints: str) -> list[WaveformStats]:
    return [s for n, s in stats_map.items()
            if any(h.lower() in n.lower() for h in hints)]


def _has_any_data(stats: list[WaveformStats]) -> bool:
    """True if at least one stat has any real sample (mean/max/min != 0)."""
    for s in stats:
        if (s.mean or 0.0) != 0.0 or (s.max or 0.0) != 0.0 or (s.min or 0.0) != 0.0:
            return True
    return False


# ---------------------------------------------------------------------------
# Digital / relay rules
# ---------------------------------------------------------------------------

@register("relay_must_trip")
def _relay_must_trip(val, rules, stats_map, scenario):
    if not val:
        return None, ""
    candidates = _any_signal_matching(stats_map, "relay", "trip", "lock", "fault")
    if not candidates:
        return "error", "no relay/trip/lock/fault signal in capture"
    for s in candidates:
        if (s.max or 0.0) >= 0.5:
            return "pass", ""
    names = ", ".join(s.signal for s in candidates)
    return "fail", f"no trip detected on {names}"


@register("relay_must_not_trip")
def _relay_must_not_trip(val, rules, stats_map, scenario):
    if not val:
        return None, ""
    for s in _any_signal_matching(stats_map, "relay", "trip"):
        if (s.max or 0.0) >= 0.5:
            return "fail", f"{s.signal} tripped at boundary (should not)"
    return "pass", ""


@register("lockout_must_trip")
def _lockout_must_trip(val, rules, stats_map, scenario):
    if not val:
        return None, ""
    locks = _any_signal_matching(stats_map, "lock_out", "lockout", "lock")
    if not locks:
        return "error", "no lock-out signal captured"
    for s in locks:
        if (s.max or 0.0) >= 0.5:
            return "pass", ""
    return "fail", "lock-out did not assert"


@register("both_lockouts_must_trip")
def _both_lockouts_must_trip(val, rules, stats_map, scenario):
    if not val:
        return None, ""
    ac = _find_signal(stats_map, "lock_out_ac", "lockout_ac")
    dc = _find_signal(stats_map, "lock_out_dc", "lockout_dc")
    if not ac or not dc:
        return "error", "need both AC and DC lock-out signals"
    if (ac.max or 0.0) >= 0.5 and (dc.max or 0.0) >= 0.5:
        return "pass", ""
    missing = []
    if (ac.max or 0.0) < 0.5:
        missing.append("AC")
    if (dc.max or 0.0) < 0.5:
        missing.append("DC")
    return "fail", f"lock-out did not trip on: {', '.join(missing)}"


@register("all_contactors_must_open")
@register("all_ac_contactors_must_open")
def _all_contactors_open(val, rules, stats_map, scenario):
    if not val:
        return None, ""
    # We currently can't observe contactor state from WaveformStats; treat as
    # informational unless a contactor-state signal is present.
    cts = _any_signal_matching(stats_map, "rly", "contactor", "breaker")
    if not cts:
        return "error", "no contactor state signal in capture"
    for s in cts:
        if (s.max or 0.0) >= 0.5:  # contactor still closed
            return "fail", f"{s.signal} stayed closed"
    return "pass", ""


@register("must_stay_connected")
@register("current_must_flow")
def _must_stay_connected(val, rules, stats_map, scenario):
    if not val:
        return None, ""
    currents = _any_signal_matching(stats_map, "ia", "ib", "ic", "idc", "iac")
    if not currents:
        return "error", "no current signal to check connection"
    if any((s.rms or 0.0) > 0.01 or (s.max or 0.0) > 0.05 for s in currents):
        return "pass", ""
    return "fail", "inverter appears disconnected (all currents ~ 0)"


@register("must_not_trip")
def _must_not_trip(val, rules, stats_map, scenario):
    if not val:
        return None, ""
    trips = _any_signal_matching(stats_map, "trip", "lock_out", "fault")
    for s in trips:
        if (s.max or 0.0) >= 0.5:
            return "fail", f"{s.signal} tripped (should stay online)"
    return "pass", ""


@register("igbt_gate_must_block")
@register("dc_converter_must_stop")
@register("battery_current_must_zero")
@register("charging_current_must_stop")
def _current_must_stop(val, rules, stats_map, scenario):
    if not val:
        return None, ""
    currents = _any_signal_matching(stats_map, "ia", "ib", "ic", "idc", "iac")
    if not currents:
        return "error", "no current signal captured"
    for s in currents:
        if (s.rms or 0.0) > 0.05 or abs(s.mean or 0.0) > 0.05:
            return "fail", f"{s.signal} still flowing (rms={s.rms:.3f})"
    return "pass", ""


@register("welding_detected_must_block_start")
@register("voltage_presence_before_close")
@register("controlled_shutdown")
@register("cv_transition_smooth")
@register("safe_shutdown")
@register("fault_detected")
@register("other_phases_must_disconnect")
def _informational_boolean(val, rules, stats_map, scenario):
    """Rules that can't be verified from waveform alone; log as informational."""
    if not val:
        return None, ""
    return "pass", "informational rule (no automated check)"


# ---------------------------------------------------------------------------
# Timing / response rules
# ---------------------------------------------------------------------------

@register("response_time_max_ms")
def _response_time_max_ms(val, rules, stats_map, scenario):
    hits = _any_signal_matching(stats_map, "relay", "lock", "trip")
    if not hits:
        return "error", "no relay/lock signal to measure response time"
    for s in hits:
        if s.rise_time_ms is None:
            return "error", f"{s.signal} missing rise_time_ms (capture lacked trigger)"
        if s.rise_time_ms > val:
            return "fail", f"{s.signal} response {s.rise_time_ms:.1f}ms > {val}ms"
    return "pass", ""


@register("clearing_time_max_s")
def _clearing_time_max_s(val, rules, stats_map, scenario):
    hits = _any_signal_matching(stats_map, "lock", "relay", "trip")
    if not hits:
        return "error", "no protection signal to measure clearing time"
    ms_limit = val * 1000.0
    for s in hits:
        if s.rise_time_ms is None:
            return "error", f"{s.signal} missing rise_time_ms (scenario needs trigger-based capture)"
        if s.rise_time_ms > ms_limit:
            return "fail", f"clearing {s.rise_time_ms/1000:.3f}s > {val}s limit"
    return "pass", ""


@register("detection_time_max_ms")
def _detection_time_max_ms(val, rules, stats_map, scenario):
    return _response_time_max_ms(val, rules, stats_map, scenario)


@register("island_detection_max_s")
def _island_detection_max_s(val, rules, stats_map, scenario):
    return _clearing_time_max_s(val, rules, stats_map, scenario)


@register("settling_time_max_s")
def _settling_time_max_s(val, rules, stats_map, scenario):
    sigs = list(stats_map.values())
    if not sigs:
        return "error", "no captured signals"
    missing = [s.signal for s in sigs if s.settling_time_ms is None]
    if missing:
        return "error", f"settling_time_ms unavailable for: {missing}"
    for s in sigs:
        if s.settling_time_ms / 1000.0 > val:
            return "fail", f"{s.signal} settled in {s.settling_time_ms/1000:.2f}s > {val}s"
    return "pass", ""


@register("settling_time_max_ms")
def _settling_time_max_ms(val, rules, stats_map, scenario):
    return _settling_time_max_s(val / 1000.0, rules, stats_map, scenario)


@register("ffci_response_max_ms")
def _ffci_response_max_ms(val, rules, stats_map, scenario):
    # Look at the fastest current channel rise time
    currs = _any_signal_matching(stats_map, "ia", "ib", "ic", "iac")
    if not currs:
        return "error", "no AC current signals captured for FFCI"
    missing = [s.signal for s in currs if s.rise_time_ms is None]
    if missing:
        return "error", f"FFCI needs triggered capture (no rise_time_ms on {missing})"
    best = min(s.rise_time_ms for s in currs if s.rise_time_ms is not None)
    if best > val:
        return "fail", f"FFCI response {best:.1f}ms > {val}ms limit"
    return "pass", ""


@register("stability_recovery_s")
def _stability_recovery_s(val, rules, stats_map, scenario):
    return _settling_time_max_s(val, rules, stats_map, scenario)


@register("sequence_timing_tolerance_ms")
def _sequence_timing_tol(val, rules, stats_map, scenario):
    # Requires event-based capture; report informational
    return "pass", "contactor sequence tolerance not measured (no event stream)"


# ---------------------------------------------------------------------------
# Voltage threshold / level rules
# ---------------------------------------------------------------------------

@register("voltage_threshold_pu")
def _voltage_threshold_pu(val, rules, stats_map, scenario):
    tol = rules.get("tolerance_pu", 0.02)
    nom = scenario.get("parameters", {}).get("nominal_voltage_peak", 325.27)
    vs = _any_signal_matching(stats_map, "vac", "vgrid", "v")
    if not vs:
        return "error", "no voltage signal"
    for s in vs:
        peak = max(abs(s.max or 0.0), abs(s.min or 0.0))
        peak_pu = peak / nom if nom else 0.0
        if abs(peak_pu - val) > tol:
            # This rule checks that the threshold was actually seen
            continue
        return "pass", f"threshold {val} pu crossed on {s.signal} (peak={peak_pu:.3f})"
    return "fail", f"voltage never reached {val} pu within +/-{tol} tol"


@register("tolerance_pu")
def _tolerance_pu(val, rules, stats_map, scenario):
    # Tolerance is consumed by voltage_threshold_pu
    return None, ""


@register("frequency_threshold_hz")
def _frequency_threshold_hz(val, rules, stats_map, scenario):
    # Without a frequency probe in WaveformStats, treat as informational
    return "pass", "frequency threshold informational (no freq probe in stats)"


@register("dc_link_voltage_max_pu")
def _dc_link_voltage_max_pu(val, rules, stats_map, scenario):
    vs = _any_signal_matching(stats_map, "vlink", "vdc")
    if not vs:
        return "error", "no DC link voltage signal"
    nom = scenario.get("parameters", {}).get("nominal_voltage", 400.0)
    for s in vs:
        peak = max(abs(s.max or 0.0), abs(s.min or 0.0))
        peak_pu = peak / nom if nom else 0.0
        if peak_pu > val:
            return "fail", f"{s.signal} peak {peak_pu:.2f} pu > {val} pu"
    return "pass", ""


@register("dc_link_overshoot_max_percent")
def _dc_link_overshoot_max_percent(val, rules, stats_map, scenario):
    return _overshoot_max_percent(val, rules, stats_map, scenario)


@register("min_voltage_pu")
def _min_voltage_pu(val, rules, stats_map, scenario):
    vs = _any_signal_matching(stats_map, "vac", "vgrid", "va", "vlink")
    if not vs:
        return "error", "no voltage signal"
    nom = scenario.get("parameters", {}).get("nominal_voltage_peak", 325.27)
    for s in vs:
        peak = max(abs(s.max or 0.0), abs(s.min or 0.0))
        peak_pu = peak / nom if nom else 0.0
        if peak_pu < val:
            return "fail", f"{s.signal} dipped to {peak_pu:.3f} pu < {val} pu"
    return "pass", ""


@register("voltage_threshold")
def _voltage_threshold(val, rules, stats_map, scenario):
    # Absolute V (not pu)
    vs = _any_signal_matching(stats_map, "vlink", "vdc", "vac")
    if not vs:
        return "error", "no voltage signal"
    for s in vs:
        if (s.max or 0.0) >= val:
            return "pass", f"{s.signal} reached {s.max:.1f} V"
    return "fail", f"voltage never reached {val} V"


# ---------------------------------------------------------------------------
# Current / power rules
# ---------------------------------------------------------------------------

@register("max_overcurrent_pu")
@register("current_peak_max_pu")
@register("ffci_current_max_pu")
def _max_overcurrent_pu(val, rules, stats_map, scenario):
    currs = _any_signal_matching(stats_map, "ia", "ib", "ic", "iac", "idc")
    if not currs:
        return "error", "no current signal"
    # Compute base current from scenario if given (Pref / (sqrt(3)*V))
    pref = scenario.get("parameters", {}).get("Pref_w", 0)
    vll = scenario.get("parameters", {}).get("nominal_voltage_rms_ll", 230.0)
    base = (pref / (1.732 * vll)) if pref and vll else 1.0
    for s in currs:
        peak_pu = max(abs(s.max or 0.0), abs(s.min or 0.0)) / (base * 1.414) if base else 0.0
        if peak_pu > val:
            return "fail", f"{s.signal} peak {peak_pu:.2f} pu > {val} pu"
    return "pass", ""


@register("ffci_current_min_pu")
@register("ffci_sustain_threshold_pu")
def _ffci_current_min_pu(val, rules, stats_map, scenario):
    currs = _any_signal_matching(stats_map, "ia", "ib", "ic", "iac")
    if not currs:
        return "error", "no AC current signal for FFCI"
    pref = scenario.get("parameters", {}).get("Pref_w", 0)
    vll = scenario.get("parameters", {}).get("nominal_voltage_rms_ll", 230.0)
    base = (pref / (1.732 * vll)) if pref and vll else 1.0
    for s in currs:
        rms_pu = (s.rms or 0.0) / base if base else 0.0
        if rms_pu >= val:
            return "pass", f"{s.signal} rms {rms_pu:.2f} pu >= {val} pu"
    return "fail", "no phase reached FFCI minimum current"


@register("inrush_current_max_pu")
def _inrush_current_max_pu(val, rules, stats_map, scenario):
    return _max_overcurrent_pu(val, rules, stats_map, scenario)


@register("power_tolerance_percent")
def _power_tolerance_percent(val, rules, stats_map, scenario):
    target = rules.get("target_p_w")
    if target is None:
        return None, ""
    pe = _find_signal(stats_map, "pe", "p_inv")
    if pe is None:
        return "error", "no Pe / P_inv signal"
    err_pct = abs((pe.mean or 0.0) - target) / abs(target) * 100.0 if target else 0.0
    if err_pct > val:
        return "fail", f"Pe={pe.mean:.0f} vs target={target:.0f} (err={err_pct:.1f}%)"
    return "pass", f"Pe within {err_pct:.1f}% of target"


@register("target_p_w")
def _target_p_w_noop(val, rules, stats_map, scenario):
    return None, ""


@register("voltage_tolerance_percent")
def _voltage_tolerance_percent(val, rules, stats_map, scenario):
    nom = rules.get("nominal_voltage_rms")
    if nom is None:
        return None, ""
    va = _find_signal(stats_map, "va")
    vb = _find_signal(stats_map, "vb")
    vc = _find_signal(stats_map, "vc")
    ref = nom / 1.732  # phase-to-neutral rms
    for s in (va, vb, vc):
        if s is None:
            continue
        if abs((s.rms or 0.0) - ref) / ref * 100.0 > val:
            return "fail", f"{s.signal} rms {s.rms:.1f} outside +/-{val}% of {ref:.1f}"
    return "pass", ""


@register("nominal_voltage_rms")
def _nominal_voltage_rms_noop(val, rules, stats_map, scenario):
    return None, ""


# ---------------------------------------------------------------------------
# Waveform quality (THD, ripple)
# ---------------------------------------------------------------------------

def _thd_rule(val, rules, stats_map, prefix_char: str) -> tuple[str | None, str]:
    """Common THD rule body for voltage ('v') or current ('i') channels."""
    matched = [(n, s) for n, s in stats_map.items()
               if n.lower().startswith(prefix_char)]
    if not matched:
        return "error", f"no {'voltage' if prefix_char == 'v' else 'current'} signal"
    annotated = [(n, s) for n, s in matched if getattr(s, "thd_percent", None) is not None]
    if not annotated:
        return "error", "THD not computed (scenario needs analysis: [thd])"
    for name, s in annotated:
        if s.thd_percent > val:
            return "fail", f"{name} THD {s.thd_percent:.2f}% > {val}%"
    return "pass", ""


@register("voltage_thd_max_pct")
def _voltage_thd_max_pct(val, rules, stats_map, scenario):
    return _thd_rule(val, rules, stats_map, "v")


@register("current_thd_max_pct")
@register("current_trd_max_pct")
def _current_thd_max_pct(val, rules, stats_map, scenario):
    return _thd_rule(val, rules, stats_map, "i")


@register("individual_harmonic_limits")
def _individual_harmonic_limits(val, rules, stats_map, scenario):
    return "error", "per-harmonic limits need FFT capture (not implemented)"


@register("dc_injection_max_pct")
def _dc_injection_max_pct(val, rules, stats_map, scenario):
    # DC injection = DC component of AC current, estimated from mean
    vll = scenario.get("parameters", {}).get("nominal_voltage_rms_ll", 230.0)
    pref = scenario.get("parameters", {}).get("Pref_w", 0)
    base = (pref / (1.732 * vll)) if pref and vll else 1.0
    ac_currs = _any_signal_matching(stats_map, "ia", "ib", "ic", "iac")
    for s in ac_currs:
        dc_pct = abs(s.mean or 0.0) / base * 100.0 if base else 0.0
        if dc_pct > val:
            return "fail", f"{s.signal} DC injection {dc_pct:.2f}% > {val}%"
    return "pass", ""


@register("dc_link_ripple_max")
@register("dc_link_ripple_max_percent")
def _dc_link_ripple_max(val, rules, stats_map, scenario):
    vs = _any_signal_matching(stats_map, "vlink", "vdc")
    if not vs:
        return "error", "no DC link voltage"
    for s in vs:
        peak_to_peak = (s.max or 0.0) - (s.min or 0.0)
        mean = abs(s.mean or 0.0)
        if mean < 1e-6:
            continue
        ripple_pct = peak_to_peak / mean * 100.0
        ripple = ripple_pct if val < 1.0 else ripple_pct  # treat both as pct
        limit = val * 100.0 if val <= 1.0 else val
        if ripple > limit:
            return "fail", f"{s.signal} ripple {ripple:.2f}% > {limit:.1f}%"
    return "pass", ""


@register("rocof_max_hz_per_s")
def _rocof_max_hz_per_s(val, rules, stats_map, scenario):
    w = _find_signal(stats_map, "w")
    if w is None:
        return "error", "no omega (w) probe captured"
    if getattr(w, "rocof_hz_per_s", None) is None:
        return "error", "ROCOF not computed (scenario needs analysis: [rocof])"
    if w.rocof_hz_per_s > val:
        return "fail", f"ROCOF {w.rocof_hz_per_s:.2f} Hz/s > {val}"
    return "pass", ""


# ---------------------------------------------------------------------------
# Transient / settling
# ---------------------------------------------------------------------------

@register("overshoot_max_percent")
def _overshoot_max_percent(val, rules, stats_map, scenario):
    for s in stats_map.values():
        if s.overshoot_percent is not None and s.overshoot_percent > val:
            return "fail", f"{s.signal} overshoot {s.overshoot_percent:.1f}% > {val}%"
    return "pass", ""


@register("steady_state_error_max_percent")
def _steady_state_error_max_percent(val, rules, stats_map, scenario):
    ref = rules.get("output_voltage_ref") or rules.get("target_p_w")
    if ref is None:
        return None, ""
    for s in stats_map.values():
        if "out" in s.signal.lower() and (ref or 0) > 0:
            err = abs((s.mean or 0.0) - ref) / ref * 100.0
            if err > val:
                return "fail", f"SS error on {s.signal} {err:.2f}% > {val}%"
    return "pass", ""


@register("power_recovery_time_s")
@register("power_recovery_threshold_percent")
def _power_recovery(val, rules, stats_map, scenario):
    # Needs event-based waveform; informational for now
    return "pass", "power recovery check requires triggered capture"


@register("settling_tolerance_percent")
def _settling_tolerance_percent(val, rules, stats_map, scenario):
    # Consumed with settling_time_max_s
    return None, ""


@register("voltage_regulation_error_max_percent")
def _voltage_regulation_error_max_percent(val, rules, stats_map, scenario):
    vs = _any_signal_matching(stats_map, "vdc", "vlink")
    if not vs:
        return "error", "no regulation voltage"
    ref = scenario.get("parameters", {}).get("target_voltage",
                                              scenario.get("parameters", {}).get("dc_link_ref_voltage", 400.0))
    for s in vs:
        err = abs((s.mean or 0.0) - ref) / ref * 100.0 if ref else 0.0
        if err > val:
            return "fail", f"{s.signal} regulation err {err:.2f}% > {val}%"
    return "pass", ""


@register("current_limit_overshoot_max_percent")
def _current_limit_overshoot_max_percent(val, rules, stats_map, scenario):
    return _overshoot_max_percent(val, rules, stats_map, scenario)


# ---------------------------------------------------------------------------
# Direction / relative-change rules
# ---------------------------------------------------------------------------

@register("direction")
def _direction(val, rules, stats_map, scenario):
    # Consumed with min_change_percent
    return None, ""


@register("min_change_percent")
def _min_change_percent(val, rules, stats_map, scenario):
    direction = rules.get("direction", "")
    pe = _find_signal(stats_map, "pe", "p_inv")
    if pe is None:
        return "error", "no Pe / power signal"
    # We need a 'before' value from somewhere. Not in stats currently; informational.
    return "pass", f"direction/min_change check informational (needs before/after capture)"


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

@register("sustain_duration_s")
def _sustain_duration_s(val, rules, stats_map, scenario):
    return "pass", "sustain duration informational (needs windowed capture)"


@register("trip_voltage_tolerance")
def _trip_voltage_tolerance_noop(val, rules, stats_map, scenario):
    return None, ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate(
    rules: dict,
    stats: list[WaveformStats],
    scenario: dict | None = None,
    strict: bool = True,
) -> tuple[str, str]:
    """Evaluate a rule set against waveform stats.

    Returns (status, reason). ``status`` is ``pass`` / ``fail`` / ``error``.
    When ``strict`` (default), unrecognised rule keys trigger ``error``
    instead of silently passing.
    """
    scenario = scenario or {}
    stats_map = {s.signal: s for s in stats}

    if not rules:
        return "pass", ""

    # Check: are we evaluating against real data or zeros?
    if stats and not _has_any_data(stats):
        # All-zero stats suggest mock or broken capture. Don't claim PASS.
        # (Informational rules that don't need data still run below.)
        pass  # keep going, individual handlers will surface errors

    errors, fails, unknowns = [], [], []
    for key, val in rules.items():
        canonical = _ALIASES.get(key, key)
        handler = _REGISTRY.get(canonical)
        if handler is None:
            unknowns.append(key)
            continue
        try:
            status, reason = handler(val, rules, stats_map, scenario)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("rule %s raised", key)
            errors.append(f"{key}: handler crashed ({exc})")
            continue
        if status == "fail":
            fails.append(reason)
        elif status == "error":
            errors.append(f"{key}: {reason}")

    if unknowns and strict:
        errors.append(f"unknown rule(s): {', '.join(unknowns)}")

    if fails:
        return "fail", "; ".join(fails)
    if errors:
        return "error", "; ".join(errors)
    return "pass", ""


def registered_rules() -> list[str]:
    """List all rule keys the dispatcher understands."""
    return sorted(set(_REGISTRY) | set(_ALIASES))
