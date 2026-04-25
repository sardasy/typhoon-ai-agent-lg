"""Allure JSON adapter tests (agent run -> Allure)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.allure_reporter import (
    _allure_status,
    _scenario_to_allure,
    write_allure_results,
)


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

class TestStatusMap:
    def test_pass_to_passed(self):
        assert _allure_status("pass") == "passed"

    def test_fail_to_failed(self):
        assert _allure_status("fail") == "failed"

    def test_error_to_broken(self):
        assert _allure_status("error") == "broken"

    def test_skipped(self):
        assert _allure_status("skipped") == "skipped"

    def test_unknown_to_broken(self):
        assert _allure_status("nonsense") == "broken"


# ---------------------------------------------------------------------------
# Single-record conversion
# ---------------------------------------------------------------------------

class TestRecordShape:
    def _convert(self, **kwargs):
        scenario = kwargs.pop("scenario", {
            "scenario_id": "vsm_x", "name": "VSM steady",
            "domain": "grid", "standard_ref": "IEEE 2800",
            "category": "voltage_source",
            "description": "VSM voltage source steady-state check",
            "parameters": {"J": 0.3, "D": 10.0, "Pref_w": 5000.0},
        })
        result = kwargs.pop("result", {
            "scenario_id": "vsm_x", "status": "pass",
            "duration_s": 1.234, "fail_reason": "",
            "retry_count": 0, "waveform_stats": [],
        })
        return _scenario_to_allure(
            scenario, result,
            suite=kwargs.pop("suite", "test suite"),
            feature=kwargs.pop("feature", "test feature"),
        )

    def test_required_top_level_keys(self):
        rec = self._convert()
        for k in ("uuid", "name", "fullName", "status", "stage",
                   "start", "stop", "labels", "parameters"):
            assert k in rec, f"missing {k}"

    def test_full_name_combines_domain_and_id(self):
        rec = self._convert()
        assert rec["fullName"] == "grid.vsm_x"

    def test_pass_has_no_status_details(self):
        rec = self._convert()
        assert "statusDetails" not in rec

    def test_fail_includes_status_details(self):
        rec = self._convert(result={
            "scenario_id": "x", "status": "fail",
            "fail_reason": "Va rms 0.5 outside +/-5%",
            "duration_s": 0.5, "waveform_stats": [{"signal": "Va"}],
            "retry_count": 1,
        })
        assert rec["status"] == "failed"
        sd = rec["statusDetails"]
        assert "Va rms" in sd["message"]
        # Trace carries the waveform stats + retry count as JSON.
        trace = json.loads(sd["trace"])
        assert trace["retry_count"] == 1

    def test_labels_include_domain_and_standard(self):
        rec = self._convert()
        labels = {l["name"]: l["value"] for l in rec["labels"]
                  if l["name"] != "tag"}
        tags = [l["value"] for l in rec["labels"] if l["name"] == "tag"]
        assert labels["domain"] == "grid"
        assert labels["epic"] == "THAA verification"
        assert "IEEE 2800" in tags
        assert "voltage_source" in tags

    def test_parameters_only_scalar_types(self):
        rec = self._convert(scenario={
            "scenario_id": "x", "name": "x", "domain": "grid",
            "parameters": {
                "scalar": 5, "string": "ok", "bool": True,
                "list": [1, 2, 3],          # excluded
                "dict": {"nested": 1},      # excluded
            },
        })
        names = {p["name"] for p in rec["parameters"]}
        assert names == {"scalar", "string", "bool"}


# ---------------------------------------------------------------------------
# write_allure_results -- end-to-end disk emission
# ---------------------------------------------------------------------------

class TestWriteAllureResults:
    def test_writes_one_file_per_result(self, tmp_path):
        scenarios = [
            {"scenario_id": "a", "name": "A", "domain": "bms"},
            {"scenario_id": "b", "name": "B", "domain": "grid"},
        ]
        results = [
            {"scenario_id": "a", "status": "pass", "duration_s": 0.1},
            {"scenario_id": "b", "status": "fail", "duration_s": 0.2,
             "fail_reason": "no trip"},
        ]
        n = write_allure_results(scenarios, results, output_dir=tmp_path)
        files = list(tmp_path.glob("*-result.json"))
        assert n == 2
        assert len(files) == 2
        # Each file is valid JSON with the required shape.
        for f in files:
            d = json.loads(f.read_text(encoding="utf-8"))
            assert d["status"] in ("passed", "failed")

    def test_unmatched_result_still_recorded(self, tmp_path):
        # Result with no matching scenario spec gets a default record.
        write_allure_results(
            scenarios=[],
            results=[{"scenario_id": "orphan", "status": "pass",
                       "duration_s": 0.05}],
            output_dir=tmp_path,
        )
        files = list(tmp_path.glob("*-result.json"))
        assert len(files) == 1
        d = json.loads(files[0].read_text(encoding="utf-8"))
        assert d["fullName"].endswith("orphan")

    def test_creates_dir_if_missing(self, tmp_path):
        target = tmp_path / "newdir" / "results"
        n = write_allure_results(
            scenarios=[], results=[
                {"scenario_id": "x", "status": "pass", "duration_s": 0.1},
            ],
            output_dir=target,
        )
        assert n == 1
        assert target.is_dir()


# ---------------------------------------------------------------------------
# generate_report node honors THAA_ALLURE_DIR env var
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_report_writes_allure_when_env_set(
    tmp_path, monkeypatch,
):
    """When THAA_ALLURE_DIR is set, generate_report drops *-result.json
    files for every scenario_result alongside the Jinja2 HTML."""
    allure_dir = tmp_path / "allure_results"
    monkeypatch.setenv("THAA_ALLURE_DIR", str(allure_dir))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "templates").mkdir()
    # Reuse the project's template; copy it next to cwd so the
    # default Reporter can find it without modification.
    import shutil
    repo_template = Path(__file__).resolve().parent.parent / "templates" / "report.html"
    shutil.copy(repo_template, tmp_path / "templates" / "report.html")

    from src.nodes.generate_report import generate_report
    state = {
        "goal": "smoke",
        "scenarios": [
            {"scenario_id": "s1", "name": "S1", "domain": "grid",
             "standard_ref": "IEEE 2800"},
        ],
        "results": [
            {"scenario_id": "s1", "status": "pass", "duration_s": 0.1,
             "waveform_stats": [], "retry_count": 0,
             "fail_reason": "", "root_cause": "",
             "corrective_action": ""},
        ],
        "standard_coverage": {},
    }
    out = await generate_report(state)
    files = list(allure_dir.glob("*-result.json"))
    assert len(files) == 1
    # Event log mentions Allure write.
    msgs = [e["message"] for e in out["events"]]
    assert any("Allure: wrote 1" in m for m in msgs)
