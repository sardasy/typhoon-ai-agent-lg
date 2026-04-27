"""HITL audit trail.

Every operator decision on a calibration write (approve / reject /
abort) is recorded here as a structured JSONL event. The trail is
append-only so a regulator-friendly log of *every* safety-critical
decision exists alongside the SQLite checkpoint stream.

Format: one JSON object per line. Schema:

    {
      "ts":          "2026-04-25T12:00:01.234Z",
      "thread_id":   "thaa-cli-1714000000",
      "operator":    "junpro2348@gmail.com" | "<USER>" | "anonymous",
      "decision":    "approve" | "reject" | "abort",
      "scenario_id": "vsm_inertia_heal_demo",
      "domain":      "grid",
      "action":      "xcp_calibration",
      "param":       "J",
      "value":       0.35,
      "confidence":  0.92,
      "root_cause":  "VSM inertia constant too low ...",
      "device_id":   "default"
    }

The default audit log lives at ``runs/hitl_audit.jsonl`` (override via
``THAA_AUDIT_PATH`` env var). Disabling: set ``THAA_AUDIT=off``.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _audit_path() -> Path:
    p = os.environ.get("THAA_AUDIT_PATH") or "runs/hitl_audit.jsonl"
    path = Path(p).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _rotated_path(base: Path) -> Path:
    """P1 #11: date-based rotation.

    ``runs/hitl_audit.jsonl`` becomes ``runs/hitl_audit-YYYY-MM.jsonl``
    -- one file per calendar month. Disable by exporting
    ``THAA_AUDIT_ROTATE=off``. Existing flat path stays the default
    (backward compat).
    """
    if (os.environ.get("THAA_AUDIT_ROTATE") or "").lower() in (
        "off", "0", "false", "no",
    ):
        return base
    yyyymm = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m")
    suffix = base.suffix or ".jsonl"
    rotated = base.with_name(f"{base.stem}-{yyyymm}{suffix}")
    rotated.parent.mkdir(parents=True, exist_ok=True)
    return rotated


def _operator_id() -> str:
    """Best-effort operator identity for the audit record."""
    if (op := os.environ.get("THAA_OPERATOR")):
        return op
    try:
        return getpass.getuser()
    except Exception:
        return "anonymous"


def _is_disabled() -> bool:
    return (os.environ.get("THAA_AUDIT") or "").lower() in ("off", "0", "false")


def record_hitl_decision(
    *,
    thread_id: str,
    decision: str,
    scenario: dict | None,
    diagnosis: dict | None,
) -> dict[str, Any]:
    """Append one HITL decision to the audit log. Returns the record.

    Never raises -- audit failures must not interrupt the run. Errors
    are logged at WARNING.
    """
    scenario = scenario or {}
    diagnosis = diagnosis or {}
    record: dict[str, Any] = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "thread_id": thread_id,
        "operator": _operator_id(),
        "decision": decision,
        "scenario_id": scenario.get("scenario_id", ""),
        "domain": scenario.get("domain", ""),
        "device_id": scenario.get("device_id", "default"),
        "action": diagnosis.get("corrective_action_type", ""),
        "param": diagnosis.get("corrective_param", ""),
        "value": diagnosis.get("corrective_value"),
        "confidence": diagnosis.get("confidence"),
        "root_cause": diagnosis.get("root_cause_description", ""),
    }
    if _is_disabled():
        return record
    try:
        path = _rotated_path(_audit_path())
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("HITL audit write failed (%s): %s", _audit_path(), exc)
    return record
