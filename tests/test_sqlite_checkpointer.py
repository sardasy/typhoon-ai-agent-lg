"""
Persistent HITL state via AsyncSqliteSaver.

Covers:
  * ``make_sqlite_checkpointer(path)`` -- sync variant used by
    ``--list-threads`` and schema setup.
  * ``acompile_graph(hitl=True, checkpoint_db=...)`` -- async compile
    that attaches an AsyncSqliteSaver; the aiosqlite connection lives
    on the returned graph's ``.checkpointer.conn``.
  * Paused state survives process restart: we build the graph once,
    pause on ``apply_fix``, close the connection, then rebuild a fresh
    graph against the same DB + thread_id and confirm the diagnosis is
    still there.
  * ``THAA_CHECKPOINT_DB`` env var is honoured.
  * ``_list_threads`` utility reads the DB and prints thread summaries.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.graph import acompile_graph, compile_graph, make_sqlite_checkpointer


def _minimal_state():
    return {
        "goal": "test", "config_path": "x.yaml", "model_path": "",
        "model_signals": [], "model_loaded": False, "rag_context": "",
        "plan_strategy": "", "scenarios": [], "scenario_index": 0,
        "estimated_duration_s": 0, "standard_coverage": {},
        "results": [], "current_scenario": None, "diagnosis": None,
        "heal_retry_count": 0, "events": [], "report_path": "",
        "error": "", "device_mode": "", "active_preset": "",
        "tse_content": "", "tse_path": "", "parsed_tse": None,
        "test_requirements": [], "generated_files": {},
        "codegen_validation": None, "export_path": "",
        "codegen_mode": "mock",
    }


async def _fake_load(state):
    return {"model_loaded": True, "model_signals": ["Va"], "events": []}


async def _fake_plan(state):
    return {
        "scenarios": [{"scenario_id": "sc1", "name": "sc1",
                        "parameters": {}, "measurements": [],
                        "pass_fail_rules": {}}],
        "scenario_index": 0, "events": [],
    }


async def _fake_execute(state):
    return {
        "current_scenario": {"scenario_id": "sc1", "name": "sc1"},
        "results": [{"scenario_id": "sc1", "status": "fail",
                     "duration_s": 0.1, "fail_reason": "mock",
                     "retry_count": 0, "waveform_stats": []}],
        "events": [],
    }


async def _fake_analyze(state):
    return {
        "diagnosis": {"failed_scenario_id": "sc1",
                       "root_cause_description": "mock",
                       "confidence": 0.9,
                       "corrective_action_type": "xcp_calibration",
                       "corrective_param": "J",
                       "corrective_value": 0.3},
        "events": [],
    }


class TestSyncFactory:
    def test_creates_db_file_and_schema(self, tmp_path):
        db = tmp_path / "chkpt.db"
        saver = make_sqlite_checkpointer(str(db))
        try:
            assert db.is_file()
            cur = saver.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='checkpoints'"
            )
            assert cur.fetchone() is not None
        finally:
            saver.conn.close()


class TestAcompileGraph:
    @pytest.mark.asyncio
    async def test_attaches_async_sqlite_saver(self, tmp_path):
        db = tmp_path / "hitl.db"
        app = await acompile_graph(hitl=True, checkpoint_db=str(db))
        try:
            assert app.checkpointer is not None
            assert hasattr(app.checkpointer, "conn")
            assert db.is_file()
        finally:
            await app.checkpointer.conn.close()

    @pytest.mark.asyncio
    async def test_env_var_picks_up_db_path(self, tmp_path):
        db = tmp_path / "from_env.db"
        with patch.dict(os.environ, {"THAA_HITL": "1",
                                     "THAA_CHECKPOINT_DB": str(db)}):
            app = await acompile_graph()
            try:
                assert app.checkpointer is not None
                assert hasattr(app.checkpointer, "conn")
            finally:
                await app.checkpointer.conn.close()
        assert db.is_file()

    def test_sync_compile_still_works_without_sqlite(self, tmp_path):
        """Regression: compile_graph() without checkpoint_db uses MemorySaver."""
        app = compile_graph(hitl=True)
        # Memory saver has no `.conn`, no need to close anything
        assert app.checkpointer is not None
        assert not hasattr(app.checkpointer, "conn")


class TestStatePersistsAcrossRestart:
    @pytest.mark.asyncio
    async def test_paused_state_persists(self, tmp_path):
        db = tmp_path / "resume.db"
        thread_id = "resume-test-1"
        cfg = {"configurable": {"thread_id": thread_id}}

        with patch("src.graph.load_model", _fake_load), \
             patch("src.graph.plan_tests", _fake_plan), \
             patch("src.graph.execute_scenario", _fake_execute), \
             patch("src.graph.analyze_failure", _fake_analyze):
            # ---- First process ----
            app1 = await acompile_graph(hitl=True, checkpoint_db=str(db))
            try:
                async for _ in app1.astream(_minimal_state(), config=cfg):
                    pass
                snap1 = await app1.aget_state(cfg)
                assert "apply_fix" in snap1.next, \
                    f"expected pause before apply_fix, got {snap1.next}"
                assert snap1.values["diagnosis"]["corrective_param"] == "J"
            finally:
                await app1.checkpointer.conn.close()

            # ---- Second process (new saver, new connection, same db) ----
            app2 = await acompile_graph(hitl=True, checkpoint_db=str(db))
            try:
                snap2 = await app2.aget_state(cfg)
                assert "apply_fix" in snap2.next, \
                    "thread did not persist across restart"
                assert snap2.values["diagnosis"]["corrective_param"] == "J"
                assert snap2.values["diagnosis"]["corrective_value"] == 0.3
            finally:
                await app2.checkpointer.conn.close()


class TestListThreadsCli:
    @pytest.mark.asyncio
    async def test_list_threads_sees_persisted_run(self, tmp_path, capsys):
        """After a paused run, `--list-threads` should print the thread."""
        from main import _list_threads
        db = tmp_path / "threads.db"
        thread_id = "list-test-1"
        cfg = {"configurable": {"thread_id": thread_id}}

        with patch("src.graph.load_model", _fake_load), \
             patch("src.graph.plan_tests", _fake_plan), \
             patch("src.graph.execute_scenario", _fake_execute), \
             patch("src.graph.analyze_failure", _fake_analyze):
            app = await acompile_graph(hitl=True, checkpoint_db=str(db))
            try:
                async for _ in app.astream(_minimal_state(), config=cfg):
                    pass
            finally:
                await app.checkpointer.conn.close()

        rc = _list_threads(str(db))
        assert rc == 0
        out = capsys.readouterr().out
        assert thread_id in out

    def test_list_threads_missing_file(self, tmp_path, capsys):
        from main import _list_threads
        rc = _list_threads(str(tmp_path / "does_not_exist.db"))
        assert rc == 1
        assert "not found" in capsys.readouterr().out
