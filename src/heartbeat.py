"""Liveness heartbeat for long-running agent regressions.

Per ``THAA_HEARTBEAT_PATH``, every meaningful state-change event in
the agent run touches a file with the current scenario_id, status,
and ISO timestamp. An external watchdog (``scripts/watchdog.py`` or
a sidecar like systemd ``WatchdogSec``) can compare the heartbeat
mtime against ``THAA_HEARTBEAT_STALE_S`` to decide if the run hung.

Schema (single JSON line, overwritten each tick):

    {
      "ts":          "2026-04-25T12:00:01.234Z",
      "node":        "execute_scenario",
      "scenario_id": "vsm_x",
      "domain":      "grid",
      "device_id":   "default",
      "passed":      12,
      "failed":      3,
      "remaining":   17,
      "pid":         34672
    }

Disable by leaving ``THAA_HEARTBEAT_PATH`` unset, or set it to
``off``.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _path() -> Path | None:
    p = os.environ.get("THAA_HEARTBEAT_PATH") or ""
    if not p or p.lower() in ("off", "0", "false"):
        return None
    path = Path(p).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def beat(*, node: str, state: dict[str, Any] | None = None) -> None:
    """Write one heartbeat tick. Best-effort; never raises."""
    target = _path()
    if target is None:
        return
    state = state or {}
    results = state.get("results") or []
    scenarios = state.get("scenarios") or []
    idx = state.get("scenario_index", 0)
    current = state.get("current_scenario") or {}

    payload = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "node": node,
        "scenario_id": current.get("scenario_id", ""),
        "domain": current.get("domain") or state.get("current_domain", ""),
        "device_id": current.get("device_id", "default"),
        "passed": sum(1 for r in results if r.get("status") == "pass"),
        "failed": sum(1 for r in results if r.get("status") == "fail"),
        "remaining": max(0, len(scenarios) - idx),
        "pid": os.getpid(),
    }
    try:
        target.write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("heartbeat write failed (%s): %s", target, exc)
