"""Tests for Phase 4-F parallel domain agents (Send-based fan-out)."""

from __future__ import annotations

import asyncio

import pytest

from src.graph_orchestrator import (
    build_parallel_orchestrator_graph,
    compile_parallel_orchestrator_graph,
    fan_out_parallel,
)
from src.parallel_agents import (
    bms_worker, general_worker, grid_worker, pcs_worker,
)
from src.tools.dut.base import HARDWARE_LOCK


# ---------------------------------------------------------------------------
# fan_out_parallel: returns one Send per non-empty domain
# ---------------------------------------------------------------------------

class TestFanOut:
    def test_one_send_per_nonempty_domain(self):
        state = {
            "scenarios": [
                {"scenario_id": "b1", "domain": "bms"},
                {"scenario_id": "b2", "domain": "bms"},
                {"scenario_id": "g1", "domain": "grid"},
            ],
            "domain_counts": {"bms": 2, "grid": 1},
        }
        sends = fan_out_parallel(state)
        nodes = sorted(s.node for s in sends)
        assert nodes == ["bms_worker", "grid_worker"]

    def test_empty_domains_get_no_send(self):
        state = {
            "scenarios": [{"scenario_id": "g1", "domain": "grid"}],
            "domain_counts": {"grid": 1},
        }
        sends = fan_out_parallel(state)
        assert len(sends) == 1
        assert sends[0].node == "grid_worker"

    def test_no_scenarios_returns_empty_list(self):
        sends = fan_out_parallel({"scenarios": [], "domain_counts": {}})
        assert sends == []

    def test_branch_state_carries_only_owned_scenarios(self):
        state = {
            "scenarios": [
                {"scenario_id": "b1", "domain": "bms"},
                {"scenario_id": "g1", "domain": "grid"},
                {"scenario_id": "g2", "domain": "grid"},
            ],
            "domain_counts": {"bms": 1, "grid": 2},
            "twin_enabled": False,
        }
        sends = fan_out_parallel(state)
        bms_send = next(s for s in sends if s.node == "bms_worker")
        grid_send = next(s for s in sends if s.node == "grid_worker")
        assert [s["scenario_id"] for s in bms_send.arg["scenarios"]] == ["b1"]
        assert [s["scenario_id"] for s in grid_send.arg["scenarios"]] == ["g1", "g2"]
        # Branch state must reset reducer-tracked lists.
        assert bms_send.arg["events"] == []
        assert bms_send.arg["results"] == []


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

class TestParallelGraphStructure:
    def test_compiles(self):
        g = compile_parallel_orchestrator_graph()
        assert g is not None

    def test_required_nodes_present(self):
        g = compile_parallel_orchestrator_graph()
        nodes = set(g.get_graph().nodes)
        for n in (
            "load_model", "plan_tests", "classify_domains",
            "bms_worker", "pcs_worker", "grid_worker", "general_worker",
            "aggregate", "generate_report",
        ):
            assert n in nodes, f"missing node {n}"

    def test_has_no_simulate_fix_node_in_graph_topology(self):
        # Workers call simulate_fix inline; it is not a parent-graph node.
        g = compile_parallel_orchestrator_graph(twin=True)
        assert "simulate_fix" not in set(g.get_graph().nodes)


# ---------------------------------------------------------------------------
# Worker behavior (in-process, no graph)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_runs_all_domain_scenarios():
    """A worker processes its scenario subset to completion."""
    state = {
        "scenarios": [
            {"scenario_id": "b1", "name": "B1", "domain": "bms",
             "parameters": {"target_cell": 1, "test_voltage": 3.6,
                            "hold_duration_s": 0.01},
             "measurements": ["V_cell_1"], "pass_fail_rules": {}},
            {"scenario_id": "b2", "name": "B2", "domain": "bms",
             "parameters": {"target_cell": 2, "test_voltage": 3.6,
                            "hold_duration_s": 0.01},
             "measurements": ["V_cell_2"], "pass_fail_rules": {}},
        ],
        "scenario_index": 0,
        "heal_retry_count": 0,
        "current_scenario": None,
        "diagnosis": None,
        "current_domain": "bms",
        "events": [],
        "results": [],
        "twin_enabled": False,
        "dut_backend": "mock",
        "dut_config": {},
    }
    out = await bms_worker(state)
    assert len(out["results"]) == 2
    assert all(r["status"] == "pass" for r in out["results"])


# ---------------------------------------------------------------------------
# End-to-end parallel run on synthetic scenario set
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parallel_orchestrator_e2e(tmp_path):
    """The full parallel graph: load_model -> plan_tests -> fan_out
    -> 2 workers in parallel -> aggregate -> generate_report."""
    cfg = tmp_path / "model.yaml"
    cfg.write_text(
        """
model:
  path: dummy.tse
scenarios:
  bms_a:
    description: BMS A
    standard_ref: IEC 62619
    parameters: {target_cell: 1, test_voltage: 3.6, hold_duration_s: 0.01}
    measurements: [V_cell_1]
    pass_fail_rules: {}
  grid_x:
    description: Grid X
    standard_ref: IEEE 1547
    parameters: {fault_template: voltage_sag, depth_pu: 0.7,
                 duration_s: 0.01, ride_through_min_s: 0.005,
                 signal_ac_sources: [Vgrid]}
    measurements: [Vgrid]
    pass_fail_rules: {}
""",
        encoding="utf-8",
    )

    from main import make_initial_state
    app = compile_parallel_orchestrator_graph()
    initial = make_initial_state("smoke", str(cfg), dut_backend="mock")

    final = await app.ainvoke(initial)

    # Both scenarios processed
    statuses = [r["status"] for r in final["results"]]
    assert len(statuses) == 2
    assert statuses.count("pass") == 2

    # Aggregator emitted per-domain summary
    assert any("Multi-agent summary" in e["message"] for e in final["events"])


# ---------------------------------------------------------------------------
# Hardware lock contract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hardware_lock_serializes_concurrent_calls():
    """Two coroutines that both want HARDWARE_LOCK must run serially."""
    order: list[str] = []

    async def task(name: str):
        async with HARDWARE_LOCK:
            order.append(f"{name}_in")
            await asyncio.sleep(0.01)
            order.append(f"{name}_out")

    await asyncio.gather(task("A"), task("B"))
    # Whichever ran first must complete before the other starts.
    assert order in (
        ["A_in", "A_out", "B_in", "B_out"],
        ["B_in", "B_out", "A_in", "A_out"],
    )
