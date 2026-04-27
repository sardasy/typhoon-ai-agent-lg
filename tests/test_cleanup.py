"""Tests for scripts/cleanup.py disk lifecycle helper."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.cleanup import (  # noqa: E402
    CleanupPlan,
    REPORT_PATTERN,
    apply as cleanup_apply,
    plan as cleanup_plan,
)


def _make_old(p: Path, days: float) -> None:
    """Backdate ``p``'s mtime by ``days`` days."""
    now = time.time()
    os.utime(p, (now - days * 86400, now - days * 86400))


@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """Point cleanup.ROOT at a tmp_path with the four output dirs prepared."""
    for sub in ("reports", "runs", "output/generated_tests", "uploads"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    import scripts.cleanup as cleanup_mod
    monkeypatch.setattr(cleanup_mod, "ROOT", tmp_path, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# REPORT_PATTERN
# ---------------------------------------------------------------------------

class TestReportPattern:
    def test_matches_canonical_filename(self):
        assert REPORT_PATTERN.match("report_20260425_120000.html")

    def test_rejects_non_canonical(self):
        assert not REPORT_PATTERN.match("report.html")
        assert not REPORT_PATTERN.match("report_2026.html")
        assert not REPORT_PATTERN.match("README.md")


# ---------------------------------------------------------------------------
# Reports retention: keep N newest
# ---------------------------------------------------------------------------

class TestReportRetention:
    def test_keeps_newest_n(self, fake_root):
        reports = fake_root / "reports"
        # Create 5 timestamped reports (different mtimes)
        names = [
            "report_20260101_000000.html",
            "report_20260102_000000.html",
            "report_20260103_000000.html",
            "report_20260104_000000.html",
            "report_20260105_000000.html",
        ]
        for i, n in enumerate(names):
            (reports / n).write_text("x", encoding="utf-8")
            _make_old(reports / n, len(names) - i)  # oldest first

        plan_obj = cleanup_plan(keep_reports=2)
        # Two newest kept (104 and 105) -- three deleted
        assert len(plan_obj.reports_to_delete) == 3
        deleted_names = {p.name for p in plan_obj.reports_to_delete}
        assert "report_20260105_000000.html" not in deleted_names
        assert "report_20260104_000000.html" not in deleted_names

    def test_skips_non_canonical_filenames(self, fake_root):
        reports = fake_root / "reports"
        (reports / "README.md").write_text("x", encoding="utf-8")
        (reports / "report_20260101_000000.html").write_text("x", encoding="utf-8")
        plan_obj = cleanup_plan(keep_reports=0)
        # Only the canonical-named report is in the deletion list.
        assert all(p.name.startswith("report_") for p in plan_obj.reports_to_delete)


# ---------------------------------------------------------------------------
# Age-based retention: runs / output / uploads
# ---------------------------------------------------------------------------

class TestAgeBasedRetention:
    def test_runs_older_than_threshold_marked(self, fake_root):
        runs = fake_root / "runs"
        old = runs / "old.sqlite"
        new = runs / "new.sqlite"
        old.write_text("x")
        new.write_text("x")
        _make_old(old, days=120)
        _make_old(new, days=10)
        plan_obj = cleanup_plan(runs_days=90)
        names = {p.name for p in plan_obj.runs_to_delete}
        assert "old.sqlite" in names
        assert "new.sqlite" not in names

    def test_output_zips_aged_out(self, fake_root):
        out = fake_root / "output" / "generated_tests"
        old = out / "v1.zip"
        new = out / "v2.zip"
        old.write_text("x")
        new.write_text("x")
        _make_old(old, days=100)
        plan_obj = cleanup_plan(output_days=60)
        assert any(p.name == "v1.zip" for p in plan_obj.output_to_delete)
        assert all(p.name != "v2.zip" for p in plan_obj.output_to_delete)


# ---------------------------------------------------------------------------
# apply() actually deletes
# ---------------------------------------------------------------------------

class TestApply:
    def test_apply_removes_files(self, fake_root):
        old = fake_root / "runs" / "ancient.sqlite"
        old.write_text("x")
        _make_old(old, days=200)
        plan_obj = cleanup_plan()
        assert old.exists()
        cleanup_apply(plan_obj)
        assert not old.exists()


# ---------------------------------------------------------------------------
# CleanupPlan helpers
# ---------------------------------------------------------------------------

class TestPlanShape:
    def test_total_count(self):
        p = CleanupPlan(
            reports_to_delete=[Path("a")],
            runs_to_delete=[Path("b"), Path("c")],
            output_to_delete=[],
            uploads_to_delete=[Path("d")],
        )
        assert p.total_count == 4
