"""Tests for Phase 4-H pre-flight checks."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


# Ensure scripts/ on path for direct import
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from scripts.preflight import (  # noqa: E402
    CheckResult,
    check_config,
    check_env,
    check_hil,
    check_rag,
    check_twin,
    check_xcp,
    run_all,
    summarize,
)


# ---------------------------------------------------------------------------
# CheckResult shape
# ---------------------------------------------------------------------------

class TestCheckResult:
    def test_repr_includes_status_icon(self):
        r = CheckResult("foo", "PASS", "ok")
        assert "[OK]" in str(r)

    def test_optional_marker(self):
        r = CheckResult("foo", "WARN", "missing", required=False)
        assert "(optional)" in str(r)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

class TestEnvCheck:
    def test_python_version_passes_on_3_12_plus(self):
        results = check_env()
        py = next(r for r in results if r.name == "python")
        assert py.status == "PASS"

    def test_deps_check_runs(self):
        results = check_env()
        deps = next(r for r in results if r.name == "deps")
        assert deps.status in ("PASS", "FAIL")


class TestConfigCheck:
    def test_missing_config_fails(self, tmp_path):
        results = check_config(str(tmp_path / "nope.yaml"))
        assert any(r.status == "FAIL" for r in results)

    def test_invalid_yaml_fails(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: : valid : yaml::", encoding="utf-8")
        # check_config resolves relative to ROOT; pass absolute via cwd shim.
        monkeypatch.setattr(
            "scripts.preflight.ROOT", tmp_path, raising=False,
        )
        results = check_config("bad.yaml")
        assert any(r.status == "FAIL" for r in results)

    def test_missing_model_path_warns(self, tmp_path, monkeypatch):
        cfg = tmp_path / "model.yaml"
        cfg.write_text("model:\n  preset: x\n", encoding="utf-8")
        monkeypatch.setattr(
            "scripts.preflight.ROOT", tmp_path, raising=False,
        )
        results = check_config("model.yaml")
        assert any(r.status == "WARN" for r in results)

    def test_present_model_path_passes(self, tmp_path, monkeypatch):
        tse = tmp_path / "models" / "x.tse"
        tse.parent.mkdir()
        tse.write_text("dummy", encoding="utf-8")
        cfg = tmp_path / "model.yaml"
        cfg.write_text("model:\n  path: models/x.tse\n", encoding="utf-8")
        monkeypatch.setattr(
            "scripts.preflight.ROOT", tmp_path, raising=False,
        )
        results = check_config("model.yaml")
        path_check = next(r for r in results if r.name == "config.model.path")
        assert path_check.status == "PASS"


class TestHilCheck:
    def test_returns_list_of_results(self):
        out = check_hil()
        assert isinstance(out, list)
        assert all(isinstance(r, CheckResult) for r in out)
        # In test env Typhoon API is unavailable -> WARN (optional)
        assert any(r.status in ("WARN", "PASS") for r in out)


class TestXcpCheck:
    def test_no_a2l_path_skips_a2l_check(self):
        out = check_xcp(None)
        # Either WARN (no pyxcp) or includes a SKIP for missing a2l
        skipped = [r for r in out if r.status == "SKIP"]
        warn_only = [r for r in out if r.status == "WARN" and r.name == "xcp"]
        assert skipped or warn_only

    def test_missing_a2l_path_fails_when_xcp_available(self, tmp_path, monkeypatch):
        # Force HAS_XCP=True so the a2l check runs.
        monkeypatch.setattr("src.tools.xcp_tools.HAS_XCP", True, raising=False)
        out = check_xcp(str(tmp_path / "nope.a2l"))
        assert any(r.name == "xcp.a2l" and r.status == "FAIL" for r in out)


class TestRagCheck:
    def test_returns_results(self):
        out = check_rag()
        assert all(isinstance(r, CheckResult) for r in out)


class TestTwinCheck:
    def test_singleton_present(self):
        out = check_twin()
        single = next(r for r in out if r.name == "twin.singleton")
        assert single.status == "PASS"

    def test_coverage_check_runs(self):
        out = check_twin()
        cov = next(r for r in out if r.name == "twin.coverage")
        assert cov.status in ("PASS", "WARN")


# ---------------------------------------------------------------------------
# run_all + summarize
# ---------------------------------------------------------------------------

class TestRunAll:
    def test_runs_every_section(self):
        out = run_all()
        # Must include checks from each section
        names = {r.name for r in out}
        assert "python" in names           # env
        assert any(n.startswith("rag") for n in names)
        assert any(n.startswith("twin") for n in names)

    def test_subset_selection(self):
        out = run_all(
            do_env=True, do_config=False, do_hil=False,
            do_xcp=False, do_rag=False, do_twin=False,
            do_safety=False,
        )
        names = {r.name for r in out}
        assert names == {"python", "deps"}


class TestSummarize:
    def test_all_pass_returns_zero(self, capsys):
        results = [CheckResult("a", "PASS", "ok")]
        rc = summarize(results)
        assert rc == 0

    def test_any_fail_returns_one(self, capsys):
        results = [
            CheckResult("a", "PASS", "ok"),
            CheckResult("b", "FAIL", "bad"),
        ]
        rc = summarize(results)
        assert rc == 1

    def test_strict_warn_returns_two(self, capsys):
        results = [
            CheckResult("a", "PASS", "ok"),
            CheckResult("b", "WARN", "soft", required=False),
        ]
        assert summarize(results, strict=False) == 0
        assert summarize(results, strict=True) == 2
