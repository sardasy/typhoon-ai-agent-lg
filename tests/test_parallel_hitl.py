"""Phase 4-J tests: parallel + HITL deferred-heal replay loop."""

from __future__ import annotations

import pytest

from src.graph_orchestrator import (
    build_parallel_orchestrator_graph,
    compile_parallel_orchestrator_graph,
    next_pending_fix,
    route_has_pending,
)


# ---------------------------------------------------------------------------
# Topology: hitl=True inserts the replay-loop nodes
# ---------------------------------------------------------------------------

class TestParallelHITLTopology:
    def test_hitl_off_has_no_replay_nodes(self):
        g = compile_parallel_orchestrator_graph(hitl=False)
        nodes = set(g.get_graph().nodes)
        assert "next_pending_fix" not in nodes
        assert "approve_fix" not in nodes
        assert "apply_fix" not in nodes  # workers handle apply inline

    def test_hitl_on_inserts_replay_nodes(self):
        g = compile_parallel_orchestrator_graph(hitl=True)
        nodes = set(g.get_graph().nodes)
        for n in ("next_pending_fix", "approve_fix",
                   "apply_fix", "execute_scenario"):
            assert n in nodes, f"missing {n}"

    def test_hitl_on_attaches_checkpointer(self):
        from langgraph.checkpoint.memory import MemorySaver
        g = compile_parallel_orchestrator_graph(hitl=True)
        assert isinstance(getattr(g, "checkpointer", None), MemorySaver)

    def test_hitl_on_pauses_before_approve_fix(self):
        g = compile_parallel_orchestrator_graph(hitl=True)
        interrupts = (
            getattr(g, "interrupt_before_nodes", None)
            or getattr(g, "interrupt_before", None)
        )
        assert interrupts is not None
        assert "approve_fix" in list(interrupts)

    def test_hitl_with_twin_inserts_simulate_fix(self):
        g = compile_parallel_orchestrator_graph(hitl=True, twin=True)
        assert "simulate_fix" in set(g.get_graph().nodes)

    def test_hitl_with_sqlite_persists(self, tmp_path):
        from langgraph.checkpoint.sqlite import SqliteSaver
        db = tmp_path / "p.sqlite"
        g = compile_parallel_orchestrator_graph(
            hitl=True, checkpoint_db=str(db),
        )
        assert isinstance(g.checkpointer, SqliteSaver)
        assert db.exists()


# ---------------------------------------------------------------------------
# next_pending_fix node: pop one entry, seed current_*
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_next_pending_fix_seeds_current_scenario():
    state = {
        "pending_fixes": [
            {
                "scenario": {"scenario_id": "s_grid_x", "domain": "grid"},
                "diagnosis": {
                    "corrective_action_type": "xcp_calibration",
                    "corrective_param": "J", "corrective_value": 0.35,
                },
                "domain": "grid",
            },
        ],
        "pending_fix_index": 0,
        "scenarios": [
            {"scenario_id": "s_grid_x", "domain": "grid"},
        ],
    }
    out = await next_pending_fix(state)
    assert out["current_scenario"]["scenario_id"] == "s_grid_x"
    assert out["diagnosis"]["corrective_param"] == "J"
    assert out["current_domain"] == "grid"
    assert out["pending_fix_index"] == 1
    # scenario_index points at the matching entry in state["scenarios"]
    assert out["scenario_index"] == 0


@pytest.mark.asyncio
async def test_next_pending_fix_when_queue_drained():
    state = {"pending_fixes": [{}], "pending_fix_index": 1}
    out = await next_pending_fix(state)
    # Empty update apart from the diagnostic event.
    assert "current_scenario" not in out
    assert "diagnosis" not in out


# ---------------------------------------------------------------------------
# route_has_pending
# ---------------------------------------------------------------------------

class TestRouteHasPending:
    def test_yes_when_index_below_length(self):
        state = {"pending_fixes": [{}, {}], "pending_fix_index": 0}
        assert route_has_pending(state) == "yes"

    def test_no_when_drained(self):
        state = {"pending_fixes": [{}], "pending_fix_index": 1}
        assert route_has_pending(state) == "no"

    def test_no_when_empty(self):
        assert route_has_pending({"pending_fixes": []}) == "no"


# ---------------------------------------------------------------------------
# Workers in defer-heals mode populate pending_fixes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_defers_heal_when_hitl_active(monkeypatch):
    """When hitl_active=True, the worker's analyze_failure path
    appends to pending_fixes and returns -- no inline apply_fix."""
    import json
    from src.parallel_agents import bms_worker

    # Force the mock backend's first capture to fail by overriding stats.
    canned_diag = json.dumps({
        "failed_scenario_id": "x",
        "root_cause": {"description": "off", "confidence": 0.9},
        "corrective_action": {"type": "xcp_calibration",
                               "parameter": "J", "suggested_value": 0.35},
    })

    class _Resp:
        def __init__(self, c): self.content = c

    class _LLM:
        def __init__(self, content): self.content = content
        def with_config(self, *a, **k): return self
        async def ainvoke(self, _msgs):
            return _Resp(self.content)

    fake = _LLM(canned_diag)
    from src.nodes import analyze_failure as af_mod
    monkeypatch.setattr(af_mod, "ChatAnthropic", lambda **_: fake)

    # Drive the mock HIL capture to return a fail
    from src.tools.dut.mock_backend import MockBackend
    orig_capture = MockBackend.capture
    async def fail_capture(self, signals, duration_s, analysis=None, **kw):
        return {"statistics": [{"signal": s, "mean": 0.0, "max": 0.0,
                                  "min": 0.0, "rms": 0.0,
                                  "rise_time_ms": 999.0}
                                 for s in signals], "duration_s": duration_s}
    monkeypatch.setattr(MockBackend, "capture", fail_capture)

    state = {
        "scenarios": [{
            "scenario_id": "s1", "name": "S1", "domain": "bms",
            "parameters": {"target_cell": 1, "test_voltage": 4.3,
                            "hold_duration_s": 0.01},
            # Include a relay-named signal so ``relay_must_trip`` can
            # find a candidate; mock max=0 then fails the rule.
            "measurements": ["V_cell_1", "BMS_OVP_relay"],
            "pass_fail_rules": {"relay_must_trip": True},
        }],
        "scenario_index": 0,
        "current_domain": "bms",
        "events": [], "results": [],
        "twin_enabled": False,
        "hitl_active": True,
        "dut_backend": "mock",
        "dut_config": {},
    }
    out = await bms_worker(state)

    # Restore for any later test in this run.
    monkeypatch.setattr(MockBackend, "capture", orig_capture)

    # The diagnosis appears on pending_fixes -- nothing was applied.
    assert len(out["pending_fixes"]) == 1
    pf = out["pending_fixes"][0]
    assert pf["scenario"]["scenario_id"] == "s1"
    assert pf["diagnosis"]["corrective_param"] == "J"
    # The worker still recorded the failed result.
    assert any(r["status"] == "fail" for r in out["results"])
