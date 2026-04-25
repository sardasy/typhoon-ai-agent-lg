"""Allure JSON adapter for agent (non-pytest) runs.

THAA's verification graph produces ``ScenarioResult`` dicts and an
``events[]`` stream. The pytest adapter (``allure-pytest``) only
catches results from ``pytest`` invocations -- agent runs miss out.

This module emits Allure-compatible ``*-result.json`` files from a
finished agent run so the same ``allure generate`` / ``allure serve``
toolchain works for both pytest and agent runs. Drop the resulting
files into the same ``--alluredir`` and Allure merges them.

Schema reference: https://allurereport.org/docs/how-it-works-test-result-file/
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _allure_status(scenario_status: str) -> str:
    """Map ScenarioResult.status -> Allure status."""
    return {
        "pass": "passed",
        "fail": "failed",
        "error": "broken",
        "skipped": "skipped",
    }.get(scenario_status, "broken")


def _scenario_to_allure(
    scenario: dict[str, Any],
    result: dict[str, Any],
    *,
    suite: str,
    feature: str,
) -> dict[str, Any]:
    """Convert one (scenario, result) pair to an Allure result dict."""
    sid = result.get("scenario_id") or scenario.get("scenario_id", "unknown")
    domain = scenario.get("domain", "general")
    status = _allure_status(result.get("status", "pass"))
    duration_ms = int(float(result.get("duration_s", 0)) * 1000)
    stop = _now_ms()
    start = stop - duration_ms

    description_parts = [
        scenario.get("description") or scenario.get("name", ""),
    ]
    if result.get("fail_reason"):
        description_parts.append(f"\n**Fail reason:** {result['fail_reason']}")
    if result.get("root_cause"):
        description_parts.append(f"\n**Root cause:** {result['root_cause']}")
    if result.get("corrective_action"):
        description_parts.append(
            f"\n**Corrective action:** {result['corrective_action']}"
        )

    labels = [
        {"name": "suite", "value": suite},
        {"name": "feature", "value": feature},
        {"name": "story", "value": scenario.get("name", sid)},
        {"name": "epic", "value": "THAA verification"},
        {"name": "domain", "value": domain},
        {"name": "framework", "value": "thaa-langgraph"},
    ]
    if scenario.get("standard_ref"):
        labels.append({"name": "tag", "value": scenario["standard_ref"]})
    if scenario.get("category"):
        labels.append({"name": "tag", "value": scenario["category"]})

    parameters: list[dict[str, str]] = []
    for k, v in (scenario.get("parameters") or {}).items():
        if isinstance(v, (str, int, float, bool)):
            parameters.append({"name": k, "value": str(v)})

    out: dict[str, Any] = {
        "uuid": str(uuid.uuid4()),
        "historyId": sid,
        "testCaseId": sid,
        "fullName": f"{domain}.{sid}",
        "name": scenario.get("name") or sid,
        "status": status,
        "stage": "finished",
        "start": start,
        "stop": stop,
        "labels": labels,
        "parameters": parameters,
        "description": "\n".join(description_parts).strip(),
    }

    if status in ("failed", "broken"):
        out["statusDetails"] = {
            "message": result.get("fail_reason") or "scenario did not pass",
            "trace": json.dumps(
                {
                    "waveform_stats": result.get("waveform_stats", []),
                    "retry_count": result.get("retry_count", 0),
                },
                ensure_ascii=False, indent=2,
            ),
        }

    return out


def write_allure_results(
    scenarios: list[dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    output_dir: str | Path,
    suite: str = "THAA agent run",
    feature: str = "self-healing verification",
) -> int:
    """Write one ``<uuid>-result.json`` per scenario into ``output_dir``.

    Returns the number of files written. Pairs each result with its
    scenario by ``scenario_id``; results without a matching scenario
    spec still get a record (the labels just fall back to defaults).
    """
    out = Path(output_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    by_id = {s.get("scenario_id"): s for s in scenarios if s.get("scenario_id")}
    written = 0
    for r in results:
        sid = r.get("scenario_id", "")
        scenario = by_id.get(sid) or {"scenario_id": sid, "name": sid}
        record = _scenario_to_allure(
            scenario, r, suite=suite, feature=feature,
        )
        path = out / f"{record['uuid']}-result.json"
        try:
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written += 1
        except OSError as exc:
            logger.warning("Allure write failed (%s): %s", path, exc)
    return written
