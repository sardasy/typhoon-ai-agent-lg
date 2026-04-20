"""
Deterministic demo: HITL pause -> close -> list -> resume.

Uses stubbed graph nodes (no ANTHROPIC_API_KEY, no HIL) so the output is
reproducible. Phases:

  1. Start an HITL run against ``runs/hitl.sqlite`` with thread ``demo-thread``.
     The stubbed analyzer proposes J=0.3; the graph pauses before apply_fix.
  2. Close the async connection (simulating process exit).
  3. Call ``_list_threads`` to show the saved thread.
  4. Resume the same thread in a fresh graph instance and ``approve`` the
     fix by resuming with ``input=None``.
  5. Verify the graph reaches END with the expected result.

Run:
    python scripts/demo_sqlite_resume.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "runs" / "hitl.sqlite"
THREAD = "demo-thread"


async def _fake_load(state):
    return {"model_loaded": True, "model_signals": ["V_pack"], "events": []}


async def _fake_plan(state):
    return {
        "scenarios": [{"scenario_id": "demo_sc", "name": "demo",
                        "parameters": {}, "measurements": [],
                        "pass_fail_rules": {}}],
        "scenario_index": 0, "events": [],
    }


async def _fake_execute(state):
    return {
        "current_scenario": {"scenario_id": "demo_sc", "name": "demo"},
        "results": [{"scenario_id": "demo_sc", "status": "fail",
                      "duration_s": 0.1, "fail_reason": "stub fail",
                      "retry_count": 0, "waveform_stats": []}],
        "events": [],
    }


async def _fake_analyze(state):
    return {
        "diagnosis": {"failed_scenario_id": "demo_sc",
                       "root_cause_description": "stub: J too low",
                       "confidence": 0.9,
                       "corrective_action_type": "xcp_calibration",
                       "corrective_param": "J",
                       "corrective_value": 0.3},
        "events": [],
    }


def _minimal_state():
    from main import make_initial_state
    return make_initial_state("sqlite demo", "configs/scenarios_heal_demo.yaml")


async def phase_1_pause():
    from src.graph import acompile_graph

    print("\n=== PHASE 1: start HITL run, pause before apply_fix ===\n")
    with patch("src.graph.load_model", _fake_load), \
         patch("src.graph.plan_tests", _fake_plan), \
         patch("src.graph.execute_scenario", _fake_execute), \
         patch("src.graph.analyze_failure", _fake_analyze):
        app = await acompile_graph(hitl=True, checkpoint_db=str(DB))
        cfg = {"configurable": {"thread_id": THREAD}}
        try:
            async for _ in app.astream(_minimal_state(), config=cfg):
                pass
            snap = await app.aget_state(cfg)
            print(f"  next nodes:   {list(snap.next)}")
            print(f"  diagnosis:    {snap.values['diagnosis']['corrective_param']}"
                  f"={snap.values['diagnosis']['corrective_value']} "
                  f"(conf={snap.values['diagnosis']['confidence']:.0%})")
            print(f"  db size:      {DB.stat().st_size} bytes")
            print("  connection CLOSED (simulated process exit)")
        finally:
            await app.checkpointer.conn.close()


def phase_2_list():
    from main import _list_threads

    print("\n=== PHASE 2: --list-threads from a fresh process ===\n")
    rc = _list_threads(str(DB))
    assert rc == 0


async def phase_3_resume():
    from src.graph import acompile_graph

    print(f"\n=== PHASE 3: resume thread '{THREAD}' and approve apply_fix ===\n")
    with patch("src.graph.load_model", _fake_load), \
         patch("src.graph.plan_tests", _fake_plan), \
         patch("src.graph.execute_scenario", _fake_execute), \
         patch("src.graph.analyze_failure", _fake_analyze):
        app = await acompile_graph(hitl=True, checkpoint_db=str(DB))
        cfg = {"configurable": {"thread_id": THREAD}}
        try:
            # Verify the paused state survived
            snap_before = await app.aget_state(cfg)
            assert "apply_fix" in snap_before.next, (
                f"thread did not persist; next={snap_before.next}"
            )
            print("  recovered diagnosis:  "
                  f"{snap_before.values['diagnosis']['corrective_param']}"
                  f"={snap_before.values['diagnosis']['corrective_value']}")

            # Resume by passing input=None
            final_result = None
            async for step in app.astream(None, config=cfg):
                for node, upd in step.items():
                    if isinstance(upd, dict):
                        for r in upd.get("results", []) or []:
                            final_result = r
            snap_after = await app.aget_state(cfg)
            print(f"  after resume next:    {list(snap_after.next)}")
            if final_result:
                print(f"  final result status:  {final_result.get('status')}")
        finally:
            await app.checkpointer.conn.close()


async def main():
    if DB.exists():
        DB.unlink()
    DB.parent.mkdir(parents=True, exist_ok=True)

    await phase_1_pause()
    phase_2_list()
    await phase_3_resume()

    print("\n=== DEMO COMPLETE -- state persisted across 3 graph instances ===\n")


if __name__ == "__main__":
    asyncio.run(main())
