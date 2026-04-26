"""Claude API cost guard + diagnosis decision cache.

Two protections against runaway LLM spend in long agent runs:

1. **Per-run hard cap** (``THAA_MAX_CLAUDE_CALLS_PER_RUN``) -- once
   exceeded, ``analyze_failure`` returns a synthetic ``escalate``
   diagnosis instead of calling Claude. Default 200 (safe for a
   ~30-scenario run with 3 retries each).

2. **Decision cache** (``THAA_DIAGNOSIS_CACHE_PATH``) -- diagnoses
   keyed by ``(scenario_id, failed_result_signature)`` are persisted
   on disk. Identical failures hit the cache instead of Claude.
   Default ``runs/diagnosis_cache.jsonl``. Disable with
   ``THAA_DIAGNOSIS_CACHE=off``.

Both controls are no-ops unless explicitly configured -- existing
runs are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Process-wide call counter. Reset by ``reset_call_count()`` between
# independent CLI invocations (tests + main both call this).
_CALL_COUNT = 0
_CALL_COUNT_LOCK = threading.Lock()


def reset_call_count() -> None:
    """Test helper / start-of-run reset."""
    global _CALL_COUNT
    with _CALL_COUNT_LOCK:
        _CALL_COUNT = 0


def claude_calls_remaining() -> int:
    """Calls allowed before the hard cap kicks in."""
    cap = int(os.environ.get("THAA_MAX_CLAUDE_CALLS_PER_RUN", "200"))
    with _CALL_COUNT_LOCK:
        return max(0, cap - _CALL_COUNT)


def consume_one_call() -> bool:
    """Increment the call counter. Returns True if the call should
    proceed; False once the per-run cap is reached."""
    cap_str = os.environ.get("THAA_MAX_CLAUDE_CALLS_PER_RUN", "200")
    try:
        cap = int(cap_str)
    except ValueError:
        cap = 200

    global _CALL_COUNT
    with _CALL_COUNT_LOCK:
        if _CALL_COUNT >= cap:
            return False
        _CALL_COUNT += 1
        return True


# ---------------------------------------------------------------------------
# Decision cache
# ---------------------------------------------------------------------------

def _is_cache_disabled() -> bool:
    return (os.environ.get("THAA_DIAGNOSIS_CACHE") or "").lower() in (
        "off", "0", "false", "no",
    )


def _cache_path() -> Path:
    p = os.environ.get("THAA_DIAGNOSIS_CACHE_PATH") or "runs/diagnosis_cache.jsonl"
    path = Path(p).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _result_signature(failed_result: dict) -> str:
    """Stable hash over the inputs that determine a diagnosis.

    Uses scenario_id + status + fail_reason + waveform_stats summary.
    Two identical failures across runs produce the same signature.
    """
    payload = {
        "scenario_id": failed_result.get("scenario_id", ""),
        "status": failed_result.get("status", ""),
        "fail_reason": failed_result.get("fail_reason", ""),
        # Stats canonicalised: sort keys, round floats to 4dp so
        # micro-noise doesn't bust the cache.
        "stats": _canonical_stats(failed_result.get("waveform_stats", [])),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _canonical_stats(stats: list[dict]) -> list[dict]:
    out = []
    for s in stats or []:
        norm = {}
        for k, v in (s or {}).items():
            if isinstance(v, float):
                norm[k] = round(v, 4)
            else:
                norm[k] = v
        out.append(dict(sorted(norm.items())))
    return sorted(out, key=lambda x: x.get("signal", ""))


def lookup_cached_diagnosis(
    scenario_id: str, failed_result: dict,
) -> dict | None:
    """Return a previously-recorded diagnosis for this exact failure,
    or None if absent / cache disabled."""
    if _is_cache_disabled():
        return None
    sig = _result_signature(failed_result)
    key = f"{scenario_id}:{sig}"

    path = _cache_path()
    if not path.is_file():
        return None
    try:
        # JSONL: scan for the latest entry matching the key.
        match: dict | None = None
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("key") == key:
                    match = rec.get("diagnosis")
        return match
    except OSError as exc:
        logger.warning("diagnosis cache read failed (%s): %s", path, exc)
        return None


def record_cached_diagnosis(
    scenario_id: str, failed_result: dict, diagnosis: dict,
) -> None:
    """Persist a diagnosis for future runs. Best-effort."""
    if _is_cache_disabled():
        return
    sig = _result_signature(failed_result)
    record = {
        "key": f"{scenario_id}:{sig}",
        "scenario_id": scenario_id,
        "result_signature": sig,
        "diagnosis": diagnosis,
    }
    try:
        with _cache_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("diagnosis cache write failed: %s", exc)


def synthetic_escalate_diagnosis(scenario_id: str, reason: str) -> dict[str, Any]:
    """Return a diagnosis dict that ``route_after_analysis`` will
    interpret as ``escalate``. Used when the cost guard trips."""
    return {
        "failed_scenario_id": scenario_id,
        "root_cause_category": "cost_guard",
        "root_cause_description": reason,
        "confidence": 0.0,
        "corrective_action_type": "escalate",
        "corrective_param": "",
        "corrective_value": None,
        "evidence": [],
    }
