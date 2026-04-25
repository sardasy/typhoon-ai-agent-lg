"""Tests for Phase 4-B multi-agent orchestrator + domain classifier."""

from __future__ import annotations

import pytest

from src.domain_classifier import (
    ALL_DOMAINS,
    DOMAIN_ORDER,
    annotate,
    classify,
    overlay_for,
    sort_by_domain,
)
from src.graph_orchestrator import (
    _route_to_first_agent,
    acompile_orchestrator_graph,
    aggregate_results,
    build_orchestrator_graph,
    classify_domains,
    compile_orchestrator_graph,
    route_after_advance,
    route_after_exec_orch,
)


# ---------------------------------------------------------------------------
# Domain classifier
# ---------------------------------------------------------------------------

class TestClassify:
    def test_explicit_domain_wins(self):
        assert classify({"domain": "grid"}) == "grid"

    def test_iec_62619_is_bms(self):
        assert classify({"standard_ref": "IEC 62619 §7.2"}) == "bms"

    def test_ieee_1547_is_grid(self):
        assert classify({"standard_ref": "IEEE 1547 §6.4"}) == "grid"

    def test_ieee_2800_is_grid(self):
        assert classify({"standard_ref": "IEEE 2800-2022"}) == "grid"

    def test_iec_61851_is_pcs(self):
        assert classify({"standard_ref": "IEC 61851"}) == "pcs"

    def test_overvoltage_template_on_cell_signal_is_bms(self):
        s = {
            "parameters": {
                "fault_template": "overvoltage", "signal": "V_cell_5",
            }
        }
        assert classify(s) == "bms"

    def test_voltage_sag_template_is_grid(self):
        s = {"parameters": {"fault_template": "voltage_sag"}}
        assert classify(s) == "grid"

    def test_vsm_template_is_grid(self):
        s = {"parameters": {"fault_template": "vsm_pref_step"}}
        assert classify(s) == "grid"

    def test_signal_vote_bms(self):
        s = {"measurements": ["V_cell_1", "V_cell_2", "BMS_OVP_relay"]}
        assert classify(s) == "bms"

    def test_signal_vote_grid(self):
        s = {"measurements": ["Vgrid", "Pe", "Qe"]}
        assert classify(s) == "grid"

    def test_signal_vote_pcs(self):
        s = {"measurements": ["Vdc", "Iout", "Duty_top"]}
        assert classify(s) == "pcs"

    def test_unknown_falls_back_to_general(self):
        assert classify({"name": "foo"}) == "general"


class TestAnnotate:
    def test_annotate_writes_domain_in_place(self):
        scenarios = [
            {"scenario_id": "a", "standard_ref": "IEC 62619"},
            {"scenario_id": "b", "standard_ref": "IEEE 1547"},
        ]
        counts = annotate(scenarios)
        assert scenarios[0]["domain"] == "bms"
        assert scenarios[1]["domain"] == "grid"
        assert counts == {"bms": 1, "pcs": 0, "grid": 1, "general": 0}


class TestSortByDomain:
    def test_sort_orders_bms_before_grid(self):
        scenarios = [
            {"scenario_id": "g", "domain": "grid", "priority": 1},
            {"scenario_id": "b", "domain": "bms", "priority": 2},
            {"scenario_id": "p", "domain": "pcs", "priority": 3},
        ]
        out = sort_by_domain(scenarios)
        assert [s["scenario_id"] for s in out] == ["b", "p", "g"]

    def test_sort_is_stable_within_domain(self):
        scenarios = [
            {"scenario_id": "b1", "domain": "bms", "priority": 1},
            {"scenario_id": "b2", "domain": "bms", "priority": 2},
            {"scenario_id": "b3", "domain": "bms", "priority": 3},
        ]
        out = sort_by_domain(scenarios)
        assert [s["scenario_id"] for s in out] == ["b1", "b2", "b3"]


class TestOverlay:
    def test_each_domain_has_an_overlay(self):
        # All non-general domains have meaningful guidance
        for d in ("bms", "pcs", "grid"):
            assert overlay_for(d).strip() != ""

    def test_general_has_no_overlay(self):
        assert overlay_for("general") == ""

    def test_unknown_domain_returns_empty(self):
        assert overlay_for("nope") == ""


# ---------------------------------------------------------------------------
# Orchestrator routing functions
# ---------------------------------------------------------------------------

class TestRouting:
    def test_route_to_first_agent_picks_first_nonempty(self):
        state = {"domain_counts": {"bms": 0, "pcs": 0, "grid": 5, "general": 0}}
        assert _route_to_first_agent(state) == "grid"

    def test_route_to_first_agent_skips_zero_counts(self):
        state = {"domain_counts": {"bms": 2, "grid": 1}}
        assert _route_to_first_agent(state) == "bms"

    def test_route_to_first_agent_no_scenarios(self):
        state = {"domain_counts": {}}
        assert _route_to_first_agent(state) == "aggregate"

    def test_route_after_advance_dispatches_to_next_domain(self):
        scenarios = [
            {"scenario_id": "b1", "domain": "bms"},
            {"scenario_id": "g1", "domain": "grid"},
        ]
        state = {"scenarios": scenarios, "scenario_index": 1}
        assert route_after_advance(state) == "grid"

    def test_route_after_advance_done_returns_aggregate(self):
        state = {"scenarios": [{"scenario_id": "x", "domain": "bms"}],
                 "scenario_index": 1}
        assert route_after_advance(state) == "aggregate"

    def test_route_after_exec_returns_fail_when_failed_with_retries(self):
        state = {
            "results": [{"status": "fail"}],
            "heal_retry_count": 0,
        }
        assert route_after_exec_orch(state) == "fail"

    def test_route_after_exec_returns_next_when_passed(self):
        state = {"results": [{"status": "pass"}], "heal_retry_count": 0}
        assert route_after_exec_orch(state) == "next"

    def test_route_after_exec_returns_next_when_retries_exhausted(self):
        state = {"results": [{"status": "fail"}], "heal_retry_count": 3}
        assert route_after_exec_orch(state) == "next"


# ---------------------------------------------------------------------------
# Orchestrator graph structure
# ---------------------------------------------------------------------------

class TestOrchestratorStructure:
    def test_build_compiles(self):
        # If the graph is malformed, .compile() raises.
        g = build_orchestrator_graph().compile()
        assert g is not None

    def test_required_nodes_present(self):
        g = build_orchestrator_graph().compile()
        nodes = set(g.get_graph().nodes)
        for n in (
            "load_model", "plan_tests", "classify_domains",
            "bms_agent", "pcs_agent", "grid_agent", "general_agent",
            "execute_scenario", "analyze_failure", "apply_fix",
            "advance_scenario", "aggregate", "generate_report",
        ):
            assert n in nodes, f"missing node {n}"


# ---------------------------------------------------------------------------
# classify_domains node + aggregate_results node behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_classify_domains_seeds_current_domain():
    state = {
        "scenarios": [{"scenario_id": "x", "domain": "grid"}],
        "domain_counts": {"grid": 1},
    }
    out = await classify_domains(state)
    assert out["current_domain"] == "grid"
    assert any("Dispatching" in e["message"] for e in out["events"])


@pytest.mark.asyncio
async def test_aggregate_breaks_results_by_domain():
    state = {
        "scenarios": [
            {"scenario_id": "b1", "domain": "bms"},
            {"scenario_id": "g1", "domain": "grid"},
            {"scenario_id": "g2", "domain": "grid"},
        ],
        "results": [
            {"scenario_id": "b1", "status": "pass"},
            {"scenario_id": "g1", "status": "fail"},
            {"scenario_id": "g2", "status": "pass"},
        ],
    }
    out = await aggregate_results(state)
    msg = out["events"][0]["message"]
    assert "bms_agent" in msg
    assert "grid_agent" in msg
    per = out["events"][0]["data"]["per_domain"]
    assert per["bms"]["pass"] == 1
    assert per["grid"]["pass"] == 1
    assert per["grid"]["fail"] == 1


# ---------------------------------------------------------------------------
# Domain ordering invariant (used by both router + aggregator)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# End-to-end smoke: orchestrator visits each non-empty agent in order.
# All scenarios pass (mock capture returns clean stats), so analyze_failure
# is never invoked -> no Claude API call.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_e2e_visits_agents_in_order(monkeypatch, tmp_path):
    """Run the full orchestrator graph on a mock scenario set.

    Scenarios: 2 BMS + 1 grid -> bms_agent runs twice then hands off to
    grid_agent. Aggregator must report counts per domain and the ordering
    must follow the BMS-before-grid invariant.
    """
    # Build a config the predefined-scenarios path can load. The mock
    # capture path returns clean stats for any signal, so all rules pass.
    cfg = tmp_path / "model.yaml"
    cfg.write_text(
        """
model:
  path: dummy.tse
scenarios:
  bms_a:
    description: BMS scenario A
    standard_ref: IEC 62619
    parameters: {target_cell: 1, test_voltage: 3.6, hold_duration_s: 0.01}
    measurements: [V_cell_1]
    pass_fail_rules: {}
  bms_b:
    description: BMS scenario B
    standard_ref: IEC 62619
    parameters: {target_cell: 2, test_voltage: 3.6, hold_duration_s: 0.01}
    measurements: [V_cell_2]
    pass_fail_rules: {}
  grid_x:
    description: Grid scenario X
    standard_ref: IEEE 1547
    parameters: {fault_template: voltage_sag, depth_pu: 0.7,
                 duration_s: 0.01, ride_through_min_s: 0.005,
                 signal_ac_sources: [Vgrid]}
    measurements: [Vgrid]
    pass_fail_rules: {}
""",
        encoding="utf-8",
    )

    # Skip the RAG fetch (mock returns docs but we don't need them)
    # Skip writing an HTML report (the report node calls hil.stop, which the
    # mock backend handles via its execute() shim).

    from src.graph_orchestrator import compile_orchestrator_graph
    from main import make_initial_state

    app = compile_orchestrator_graph()
    initial = make_initial_state(
        "smoke", str(cfg), dut_backend="mock",
    )

    final = await app.ainvoke(initial)

    # All 3 scenarios ran, none failed.
    assert len(final["results"]) == 3
    statuses = [r["status"] for r in final["results"]]
    assert statuses.count("pass") == 3, statuses

    # Scenarios were sorted bms-before-grid by plan_tests.
    domains_in_run = [s["domain"] for s in final["scenarios"]]
    assert domains_in_run == ["bms", "bms", "grid"]

    # Aggregator emitted a per-domain summary.
    msgs = [e["message"] for e in final["events"]]
    assert any("Multi-agent summary" in m for m in msgs)


# ---------------------------------------------------------------------------
# Phase 4-D: orchestrator HITL / SQLite checkpointer support
# ---------------------------------------------------------------------------

class TestOrchestratorHITL:
    def test_compile_with_hitl_attaches_memorysaver(self):
        # When hitl=True without an explicit checkpointer, MemorySaver kicks in.
        from langgraph.checkpoint.memory import MemorySaver
        app = compile_orchestrator_graph(hitl=True)
        assert isinstance(getattr(app, "checkpointer", None), MemorySaver)

    def test_compile_without_hitl_has_no_checkpointer(self):
        app = compile_orchestrator_graph(hitl=False)
        # No checkpointer wired -> attribute either missing or None.
        assert getattr(app, "checkpointer", None) is None

    def test_compile_with_sqlite_path_uses_sqlite_saver(self, tmp_path):
        from langgraph.checkpoint.sqlite import SqliteSaver
        db = tmp_path / "orch.sqlite"
        app = compile_orchestrator_graph(
            hitl=True, checkpoint_db=str(db),
        )
        assert isinstance(app.checkpointer, SqliteSaver)
        assert db.exists()  # schema setup wrote the file

    def test_interrupt_before_apply_fix_default(self):
        app = compile_orchestrator_graph(hitl=True)
        # LangGraph stores configured interrupts on the compiled app.
        interrupts = getattr(app, "interrupt_before_nodes", None) \
            or getattr(app, "interrupt_before", None)
        # In current LangGraph versions the attribute may be a list/tuple
        # depending on internals; just assert apply_fix is in there.
        assert interrupts is not None
        assert "apply_fix" in list(interrupts)


@pytest.mark.asyncio
async def test_acompile_orchestrator_with_sqlite(tmp_path):
    """Async compile path opens an aiosqlite-backed AsyncSqliteSaver."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db = tmp_path / "orch_async.sqlite"
    app = await acompile_orchestrator_graph(
        hitl=True, checkpoint_db=str(db),
    )
    try:
        assert isinstance(app.checkpointer, AsyncSqliteSaver)
        assert db.exists()
    finally:
        # Release the file lock so tmp_path teardown can remove it.
        conn = getattr(app.checkpointer, "conn", None)
        if conn is not None and hasattr(conn, "close"):
            close = conn.close()
            if hasattr(close, "__await__"):
                await close


class TestDomainOrder:
    def test_all_domains_have_an_order(self):
        for d in ALL_DOMAINS:
            assert d in DOMAIN_ORDER

    def test_order_matches_listed_sequence(self):
        assert DOMAIN_ORDER["bms"] < DOMAIN_ORDER["pcs"]
        assert DOMAIN_ORDER["pcs"] < DOMAIN_ORDER["grid"]
        assert DOMAIN_ORDER["grid"] < DOMAIN_ORDER["general"]
