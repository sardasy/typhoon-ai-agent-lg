"""Tests for the Phase 4-C digital twin."""

from __future__ import annotations

import pytest

from src.graph import build_graph, route_after_simulation
from src.graph_orchestrator import build_orchestrator_graph
from src.twin import (
    DigitalTwin,
    PLAUSIBLE_RANGES,
    TwinPrediction,
    get_twin,
    reset_twin,
)


# ---------------------------------------------------------------------------
# DigitalTwin.predict() verdicts
# ---------------------------------------------------------------------------

def _diag(param: str, value: float, action_type: str = "xcp_calibration") -> dict:
    return {
        "corrective_action_type": action_type,
        "corrective_param": param,
        "corrective_value": value,
    }


def _scenario(rules: dict | None = None, sid: str = "s1") -> dict:
    return {
        "scenario_id": sid,
        "domain": "grid",
        "pass_fail_rules": rules or {},
    }


class TestPredictVeto:
    def test_no_op_fix_is_vetoed(self):
        twin = DigitalTwin()
        twin.commit("J", 0.35, scenario_id="s1")
        pred = twin.predict(
            scenario=_scenario(sid="s1"),
            failed_result={},
            action=_diag("J", 0.35),
        )
        assert pred.verdict == "veto"
        assert "no-op" in pred.reason

    def test_repeat_attempt_within_scenario_is_vetoed(self):
        twin = DigitalTwin()
        # First attempt
        twin.commit("J", 0.20, scenario_id="s1")
        # Second analyzer suggestion is the same value -- must veto.
        pred = twin.predict(
            scenario=_scenario(sid="s1"),
            failed_result={},
            action=_diag("J", 0.20),
        )
        assert pred.verdict == "veto"

    def test_out_of_range_is_vetoed(self):
        twin = DigitalTwin()
        # PLAUSIBLE_RANGES["J"] = (0.01, 5.0); 99.0 is way out.
        pred = twin.predict(
            scenario=_scenario(),
            failed_result={},
            action=_diag("J", 99.0),
        )
        assert pred.verdict == "veto"
        assert "out-of-range" in pred.reason

    def test_wrong_direction_is_vetoed(self):
        twin = DigitalTwin()
        twin.commit("J", 0.30)
        # rule "relay_must_trip" wants J INCREASED -- proposing a decrease
        # to 0.10 must be vetoed.
        pred = twin.predict(
            scenario=_scenario(rules={"relay_must_trip": True}),
            failed_result={},
            action=_diag("J", 0.10),
        )
        assert pred.verdict == "veto"
        assert "wrong-direction" in pred.reason


class TestPredictCommit:
    def test_first_in_range_attempt_commits(self):
        twin = DigitalTwin()
        pred = twin.predict(
            scenario=_scenario(rules={"relay_must_trip": True}),
            failed_result={},
            action=_diag("J", 0.35),
        )
        assert pred.verdict == "commit"

    def test_correct_direction_increase_commits(self):
        twin = DigitalTwin()
        twin.commit("J", 0.10)
        pred = twin.predict(
            scenario=_scenario(rules={"relay_must_trip": True}),
            failed_result={},
            action=_diag("J", 0.35),
        )
        assert pred.verdict == "commit"

    def test_param_without_range_or_direction_rules_commits(self):
        twin = DigitalTwin()
        # Some made-up param the twin has no opinion on.
        pred = twin.predict(
            scenario=_scenario(),
            failed_result={},
            action=_diag("UNKNOWN_PARAM", 42.0),
        )
        assert pred.verdict == "commit"

    def test_non_calibration_action_commits(self):
        twin = DigitalTwin()
        pred = twin.predict(
            scenario=_scenario(),
            failed_result={},
            action=_diag("anything", 1.0, action_type="retest"),
        )
        assert pred.verdict == "commit"


class TestPredictUncertain:
    def test_missing_value_is_uncertain(self):
        twin = DigitalTwin()
        pred = twin.predict(
            scenario=_scenario(),
            failed_result={},
            action={"corrective_action_type": "xcp_calibration",
                    "corrective_param": "J",
                    "corrective_value": None},
        )
        assert pred.verdict == "uncertain"


class TestPredictionDict:
    def test_to_dict_round_trips(self):
        p = TwinPrediction(
            verdict="commit", reason="ok", param="J",
            proposed_value=0.3, current_value=0.1,
            twin_state_snapshot={"J": 0.1},
        )
        d = p.to_dict()
        assert d["verdict"] == "commit"
        assert d["param"] == "J"
        assert d["proposed_value"] == 0.3
        assert d["current_value"] == 0.1
        assert d["twin_state"] == {"J": 0.1}


class TestPlausibleRanges:
    def test_writable_xcp_params_have_ranges(self):
        # Sanity check: every parameter we know how to vote on has a band.
        for p in ("J", "D", "Kv", "Ctrl_Kp", "BMS_OVP_threshold"):
            assert p in PLAUSIBLE_RANGES
            lo, hi = PLAUSIBLE_RANGES[p]
            assert lo < hi


# ---------------------------------------------------------------------------
# Graph integration
# ---------------------------------------------------------------------------

class TestGraphIntegration:
    def test_default_graph_has_no_simulate_fix_node(self):
        g = build_graph().compile()
        assert "simulate_fix" not in set(g.get_graph().nodes)

    def test_twin_graph_inserts_simulate_fix(self):
        g = build_graph(twin=True).compile()
        nodes = set(g.get_graph().nodes)
        assert "simulate_fix" in nodes

    def test_orchestrator_twin_inserts_simulate_fix(self):
        g = build_orchestrator_graph(twin=True).compile()
        nodes = set(g.get_graph().nodes)
        assert "simulate_fix" in nodes

    def test_route_after_simulation_veto(self):
        state = {"twin_prediction": {"verdict": "veto", "reason": "no-op"}}
        assert route_after_simulation(state) == "veto"

    def test_route_after_simulation_commit(self):
        state = {"twin_prediction": {"verdict": "commit", "reason": "ok"}}
        assert route_after_simulation(state) == "commit"

    def test_route_after_simulation_uncertain_falls_through_to_commit(self):
        state = {"twin_prediction": {"verdict": "uncertain"}}
        assert route_after_simulation(state) == "commit"


# ---------------------------------------------------------------------------
# simulate_fix node
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_simulate_fix_node_emits_prediction():
    from src.nodes.simulate_fix import simulate_fix

    reset_twin()
    twin = get_twin()
    twin.commit("J", 0.35, scenario_id="sim_test")

    state = {
        "current_scenario": _scenario(rules={"relay_must_trip": True}, sid="sim_test"),
        "diagnosis": _diag("J", 0.35),  # no-op
        "results": [{"status": "fail"}],
        "current_domain": "grid",
    }
    out = await simulate_fix(state)
    pred = out["twin_prediction"]
    assert pred["verdict"] == "veto"
    assert any(e["event_type"] == "thought" for e in out["events"])


@pytest.mark.asyncio
async def test_simulate_fix_node_commits_clean_proposal():
    from src.nodes.simulate_fix import simulate_fix

    reset_twin()

    state = {
        "current_scenario": _scenario(rules={"relay_must_trip": True}, sid="sim_test_2"),
        "diagnosis": _diag("J", 0.35),
        "results": [{"status": "fail"}],
        "current_domain": "grid",
    }
    out = await simulate_fix(state)
    assert out["twin_prediction"]["verdict"] == "commit"


# ---------------------------------------------------------------------------
# get_twin / reset_twin
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_twin_is_idempotent(self):
        a = get_twin()
        b = get_twin()
        assert a is b

    def test_reset_clears_state(self):
        twin = get_twin()
        twin.commit("J", 0.5)
        assert "J" in twin.state
        reset_twin()
        assert get_twin().state == {}
