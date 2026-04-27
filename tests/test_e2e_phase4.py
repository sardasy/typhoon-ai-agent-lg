"""Phase 4 end-to-end integration tests.

Each unit test in the suite covers one phase in isolation. These
exercise the **combined stack** -- DUT abstraction + multi-agent
orchestration + (where applicable) twin gating + per-domain RAG +
multi-device routing -- on synthetic scenarios that pass without an
ANTHROPIC_API_KEY (no analyzer call required since nothing fails).

The point is regression coverage on combinations the unit suite
doesn't touch:

    --orchestrator                                  -> single-graph + sort by domain
    --orchestrator --twin                           -> twin in marker-node graph
    --orchestrator --parallel                       -> Send fan-out
    --orchestrator --parallel --twin                -> twin inline in workers
    --orchestrator --parallel + multi-device YAML  -> per-device locks + fan-out
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures: build a passing 3-domain scenario set on disk.
# ---------------------------------------------------------------------------

def _three_domain_yaml(tmp_path):
    p = tmp_path / "three_domain.yaml"
    p.write_text(
        """
model:
  path: dummy.tse
scenarios:
  bms_a:
    description: BMS smoke
    standard_ref: IEC 62619
    parameters: {target_cell: 1, test_voltage: 3.6, hold_duration_s: 0.01}
    measurements: [V_cell_1]
    pass_fail_rules: {}
  pcs_b:
    description: PCS smoke
    standard_ref: IEC 61851
    parameters: {target_sensor: Vdc, test_voltage: 400, hold_duration_s: 0.01}
    measurements: [Vdc, Idc]
    pass_fail_rules: {}
  grid_x:
    description: Grid LVRT smoke
    standard_ref: IEEE 1547
    parameters:
      fault_template: voltage_sag
      depth_pu: 0.7
      duration_s: 0.01
      ride_through_min_s: 0.005
      signal_ac_sources: [Vgrid]
    measurements: [Vgrid]
    pass_fail_rules: {}
""",
        encoding="utf-8",
    )
    return p


def _multi_device_yaml(tmp_path):
    p = tmp_path / "multi_device.yaml"
    p.write_text(
        """
model:
  path: dummy.tse
scenarios:
  rig_a_bms:
    description: rig A BMS
    standard_ref: IEC 62619
    device_id: hil_404_a
    parameters: {target_cell: 1, test_voltage: 3.6, hold_duration_s: 0.01}
    measurements: [V_cell_1]
    pass_fail_rules: {}
  rig_b_bms:
    description: rig B BMS
    standard_ref: IEC 62619
    device_id: hil_404_b
    parameters: {target_cell: 1, test_voltage: 3.6, hold_duration_s: 0.01}
    measurements: [V_cell_1]
    pass_fail_rules: {}
  rig_a_grid:
    description: rig A Grid
    standard_ref: IEEE 1547
    device_id: hil_404_a
    parameters:
      fault_template: voltage_sag
      depth_pu: 0.7
      duration_s: 0.01
      ride_through_min_s: 0.005
      signal_ac_sources: [Vgrid]
    measurements: [Vgrid]
    pass_fail_rules: {}
""",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# Combined stack runs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_serial_three_domains(tmp_path):
    """4-A + 4-B: serial orchestrator with mock backend, 3 domains."""
    from main import make_initial_state
    from src.graph_orchestrator import compile_orchestrator_graph

    cfg = _three_domain_yaml(tmp_path)
    app = compile_orchestrator_graph()
    initial = make_initial_state("e2e", str(cfg), dut_backend="mock")

    final = await app.ainvoke(initial)

    assert len(final["results"]) == 3, final["results"]
    assert all(r["status"] == "pass" for r in final["results"])
    # Exactly one execution per domain bucket.
    domains = [s["domain"] for s in final["scenarios"]]
    assert sorted(domains) == ["bms", "grid", "pcs"]


@pytest.mark.asyncio
async def test_orchestrator_serial_with_twin_passing(tmp_path):
    """4-B + 4-C: twin enabled, but no failures -> simulate_fix never fires."""
    from main import make_initial_state
    from src.graph_orchestrator import compile_orchestrator_graph

    cfg = _three_domain_yaml(tmp_path)
    app = compile_orchestrator_graph(twin=True)
    initial = make_initial_state("e2e+twin", str(cfg), dut_backend="mock",
                                 twin_enabled=True)

    final = await app.ainvoke(initial)
    assert len(final["results"]) == 3
    # No twin prediction was ever computed (no failure -> no analyze -> no twin).
    assert final.get("twin_prediction") is None
    # All scenarios passed -> heal loop untouched.
    assert all(r["status"] == "pass" for r in final["results"])


@pytest.mark.asyncio
async def test_parallel_orchestrator_three_domains(tmp_path):
    """4-A + 4-B + 4-F: parallel domain workers, mock backend."""
    from main import make_initial_state
    from src.graph_orchestrator import compile_parallel_orchestrator_graph

    cfg = _three_domain_yaml(tmp_path)
    app = compile_parallel_orchestrator_graph()
    initial = make_initial_state("e2e parallel", str(cfg), dut_backend="mock")

    final = await app.ainvoke(initial)
    assert len(final["results"]) == 3
    assert all(r["status"] == "pass" for r in final["results"])

    # The parallel summary message must list every domain that had work.
    msgs = [e["message"] for e in final["events"]]
    summary = next(m for m in msgs if "Multi-agent summary" in m)
    for d in ("bms_agent", "pcs_agent", "grid_agent"):
        assert d in summary, summary


@pytest.mark.asyncio
async def test_parallel_orchestrator_with_twin(tmp_path):
    """4-F + 4-C: parallel workers honor twin state field (no veto on pass)."""
    from main import make_initial_state
    from src.graph_orchestrator import compile_parallel_orchestrator_graph

    cfg = _three_domain_yaml(tmp_path)
    app = compile_parallel_orchestrator_graph(twin=True)
    initial = make_initial_state("e2e parallel+twin", str(cfg),
                                 dut_backend="mock", twin_enabled=True)

    final = await app.ainvoke(initial)
    assert len(final["results"]) == 3
    assert all(r["status"] == "pass" for r in final["results"])


@pytest.mark.asyncio
async def test_parallel_orchestrator_multi_device(tmp_path):
    """4-F + 4-I: parallel + per-scenario device routing."""
    from main import make_initial_state
    from src.graph_orchestrator import compile_parallel_orchestrator_graph
    from src.tools.dut.base import _DEVICE_LOCKS

    cfg = _multi_device_yaml(tmp_path)
    app = compile_parallel_orchestrator_graph()
    initial = make_initial_state(
        "e2e parallel multi-device", str(cfg),
        dut_backend="mock",
        device_pool={"hil_404_a": {}, "hil_404_b": {}},
    )

    final = await app.ainvoke(initial)
    # 3 scenarios across 2 devices, all pass on mock.
    assert len(final["results"]) == 3
    assert all(r["status"] == "pass" for r in final["results"])

    # The device-id of each scenario propagated to the executor: at
    # least the two named device locks were created (lazy creation
    # only happens when a backend on that device runs I/O).
    # MockBackend doesn't take the lock, so we verify routing via
    # cached backend instances rather than locks:
    from src.nodes.load_model import _dut_singletons
    device_ids = {b.device_id for _, b in _dut_singletons.items()
                  if hasattr(b, "device_id")}
    # At minimum, both rigs must be present (default may also be
    # present from load_model.control()).
    assert "hil_404_a" in device_ids
    assert "hil_404_b" in device_ids


# ---------------------------------------------------------------------------
# Domain-classified RAG bucket population (Phase 4-G) survives the
# full pipeline -- analyze_failure is never called on this passing set,
# but load_model still pre-fetches every namespace.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_model_populates_all_domain_rag_buckets(tmp_path):
    from main import make_initial_state
    from src.graph_orchestrator import compile_orchestrator_graph

    cfg = _three_domain_yaml(tmp_path)
    app = compile_orchestrator_graph()
    initial = make_initial_state("e2e rag", str(cfg), dut_backend="mock")
    final = await app.ainvoke(initial)

    by_domain = final.get("rag_context_by_domain", {})
    assert set(by_domain.keys()) == {"bms", "pcs", "grid", "general"}
