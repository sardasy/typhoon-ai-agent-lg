"""Digital twin (Phase 4-C MVP).

A software mirror of the ECU calibration state plus a what-if predictor
that gates :mod:`src.nodes.apply_fix`. The twin's job is to filter out
clearly-bad calibration writes BEFORE they hit real hardware:

    - **No-op fix.** The analyzer is proposing the value we just wrote.
      Applying it again would burn a heal retry on the same change.
    - **Out-of-range write.** The proposed value is outside the
      plausible band for that parameter (e.g. ``J = 99.0`` -- IEEE 2800
      practical inertia is ~0.05..2.0 s).
    - **Wrong-direction write.** The failing scenario is "relay didn't
      trip" -> increasing damping (D) makes it less likely to trip.
      The twin can flag this so the analyzer is forced to escalate
      instead of looping.

The predictor is deliberately conservative: when in doubt it returns
``uncertain`` -- the graph commits the fix and lets the live test
decide. The only blocking verdict is ``veto`` (see ``TwinPrediction``).

This is **not** a high-fidelity simulator -- ``XCPBackend`` already
carries one of those for waveform generation. The twin is a cheap
sanity check that runs in microseconds, not milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .constants import ACTION_XCP_CALIBRATION

# Plausible-value bands per writable calibration parameter. Pulled from
# the live whitelist in ``src/tools/xcp_tools.py::WRITABLE_PARAMS`` and
# the IEEE 2800 / IEC 62619 typical operating ranges. Any param NOT
# listed here is treated as "no opinion" -- the twin falls back to
# ``uncertain``.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    # IEEE 2800 GFM tunables
    "J":  (0.01, 5.0),    # virtual inertia (s)
    "D":  (0.0, 50.0),    # damping ratio
    "Kv": (0.0, 5.0),     # voltage-droop gain
    # PI controller gains
    "Ctrl_Kp": (1e-4, 1e3),
    "Ctrl_Ki": (1e-4, 1e3),
    "Ctrl_Kd": (0.0, 1e3),
    # BMS thresholds (cell volts / mV scan intervals)
    "BMS_OVP_threshold": (3.5, 4.5),
    "BMS_UVP_threshold": (2.0, 3.5),
}

# Sign-of-effect rules: for a given pass/fail rule + parameter, did the
# proposed change move the parameter in the helpful direction?  Each
# entry is (param, rule_name) -> "increase" | "decrease". When the rule
# is failing (status="fail" with that rule unmet), moving the parameter
# the OPPOSITE direction is wrong-direction -> veto.
#
# Conservative rule of thumb: if a rule isn't listed, the twin doesn't
# vote on direction.
EFFECT_DIRECTION: dict[tuple[str, str], Literal["increase", "decrease"]] = {
    ("J", "relay_must_trip"):           "increase",
    ("J", "rocof_max_hz_per_s"):        "increase",
    ("D", "overshoot_max_percent"):     "increase",
    ("D", "settling_time_max_ms"):      "decrease",
    ("Kv", "steady_state_error_max_percent"): "increase",
    ("BMS_OVP_threshold", "relay_must_trip"): "decrease",
    ("Ctrl_Kp", "rise_time_max_ms"):    "increase",
    ("Ctrl_Kp", "overshoot_max_percent"): "decrease",
}


Verdict = Literal["commit", "veto", "uncertain"]


@dataclass
class TwinPrediction:
    """Result of :meth:`DigitalTwin.predict`.

    The graph routes on ``verdict``:
      - ``commit``     -> proceed to apply_fix
      - ``veto``       -> skip apply_fix, escalate to next scenario
      - ``uncertain``  -> proceed to apply_fix (twin defers to live test)
    """
    verdict: Verdict = "uncertain"
    reason: str = ""
    param: str = ""
    proposed_value: float | None = None
    current_value: float | None = None
    twin_state_snapshot: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            "param": self.param,
            "proposed_value": self.proposed_value,
            "current_value": self.current_value,
            "twin_state": dict(self.twin_state_snapshot),
        }


@dataclass
class DigitalTwin:
    """Software model of ECU calibration state + what-if predictor."""

    state: dict[str, float] = field(default_factory=dict)
    # History of write attempts per scenario_id, used to detect no-op fixes.
    # Keyed (scenario_id) -> list of (param, value) pairs the agent has
    # already tried.
    history: dict[str, list[tuple[str, float]]] = field(default_factory=dict)

    # ----- Mutators -----------------------------------------------------

    def commit(self, param: str, value: float, *, scenario_id: str = "") -> None:
        """Mirror a real apply_fix that succeeded.

        Updates the twin's calibration state AND the history (so the next
        prediction sees this attempt).
        """
        self.state[param] = float(value)
        if scenario_id:
            self.history.setdefault(scenario_id, []).append((param, float(value)))

    def reset(self) -> None:
        self.state.clear()
        self.history.clear()

    # ----- Predictor ----------------------------------------------------

    def predict(
        self,
        *,
        scenario: dict[str, Any],
        failed_result: dict[str, Any],
        action: dict[str, Any],
    ) -> TwinPrediction:
        """What-if check on the analyzer's proposed corrective action.

        ``action`` is the ``diagnosis`` dict produced by
        ``analyze_failure``; we look at ``corrective_action_type``,
        ``corrective_param``, ``corrective_value``.
        """
        action_type = action.get("corrective_action_type", "")
        param = action.get("corrective_param", "") or ""
        value = action.get("corrective_value")

        # Only XCP calibration writes are gated. Other action types fall
        # straight through (apply_fix already no-ops them).
        if action_type != ACTION_XCP_CALIBRATION:
            return TwinPrediction(
                verdict="commit",
                reason=f"non-calibration action ({action_type}), twin defers",
                param=param,
                proposed_value=value,
                twin_state_snapshot=dict(self.state),
            )

        if not param or value is None:
            return TwinPrediction(
                verdict="uncertain",
                reason="missing param/value",
                param=param,
                proposed_value=value,
                twin_state_snapshot=dict(self.state),
            )

        new_value = float(value)
        current = self.state.get(param)
        sid = scenario.get("scenario_id", "")
        snapshot = dict(self.state)

        # ---- Veto 1: no-op fix ----
        if current is not None and abs(new_value - current) < 1e-9:
            return TwinPrediction(
                verdict="veto",
                reason=(
                    f"no-op fix: {param} is already {current}, retrying "
                    "would not change controller behavior"
                ),
                param=param,
                proposed_value=new_value,
                current_value=current,
                twin_state_snapshot=snapshot,
            )

        # ---- Veto 2: same value already attempted this scenario ----
        prior = self.history.get(sid, [])
        for prev_param, prev_value in prior:
            if prev_param == param and abs(prev_value - new_value) < 1e-9:
                return TwinPrediction(
                    verdict="veto",
                    reason=(
                        f"already tried {param}={new_value} in this scenario "
                        f"({len(prior)} prior write(s)) without success"
                    ),
                    param=param,
                    proposed_value=new_value,
                    current_value=current,
                    twin_state_snapshot=snapshot,
                )

        # ---- Veto 3: out-of-range ----
        band = PLAUSIBLE_RANGES.get(param)
        if band is not None:
            lo, hi = band
            if not (lo <= new_value <= hi):
                return TwinPrediction(
                    verdict="veto",
                    reason=(
                        f"out-of-range: {param}={new_value} outside "
                        f"plausible band [{lo}, {hi}]"
                    ),
                    param=param,
                    proposed_value=new_value,
                    current_value=current,
                    twin_state_snapshot=snapshot,
                )

        # ---- Veto 4: wrong-direction ----
        # Only fires when current_value is known AND the failed scenario
        # had a rule we have a sign-rule for.
        if current is not None:
            rules = scenario.get("pass_fail_rules") or {}
            for rule_name in rules:
                want_dir = EFFECT_DIRECTION.get((param, rule_name))
                if want_dir is None:
                    continue
                delta = new_value - current
                if want_dir == "increase" and delta < 0:
                    return TwinPrediction(
                        verdict="veto",
                        reason=(
                            f"wrong-direction: rule '{rule_name}' wants "
                            f"{param} INCREASED to recover, but proposal "
                            f"decreases {current} -> {new_value}"
                        ),
                        param=param, proposed_value=new_value,
                        current_value=current, twin_state_snapshot=snapshot,
                    )
                if want_dir == "decrease" and delta > 0:
                    return TwinPrediction(
                        verdict="veto",
                        reason=(
                            f"wrong-direction: rule '{rule_name}' wants "
                            f"{param} DECREASED to recover, but proposal "
                            f"increases {current} -> {new_value}"
                        ),
                        param=param, proposed_value=new_value,
                        current_value=current, twin_state_snapshot=snapshot,
                    )

        # ---- All checks passed ----
        return TwinPrediction(
            verdict="commit",
            reason=(
                f"twin OK: {param} {current} -> {new_value} "
                f"(within plausible range, no prior attempt at this value)"
            ),
            param=param,
            proposed_value=new_value,
            current_value=current,
            twin_state_snapshot=snapshot,
        )


# ---------------------------------------------------------------------------
# Module-level singleton (one twin per process; reset between independent
# runs is the caller's responsibility -- typical use is ``get_twin().reset()``
# at run start).
# ---------------------------------------------------------------------------

_twin: DigitalTwin | None = None


def get_twin() -> DigitalTwin:
    global _twin
    if _twin is None:
        _twin = DigitalTwin()
    return _twin


def reset_twin() -> None:
    """Drop accumulated twin state. Test fixtures call this between cases."""
    global _twin
    _twin = DigitalTwin()
