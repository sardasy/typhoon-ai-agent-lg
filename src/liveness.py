"""Hardware liveness probe (P1 #10).

Detects the silent-disconnect failure mode where the HIL/ECU is
unplugged or the bridge has crashed: real signals stop varying, but
the executor still returns deterministic stats (often zeros) so
``relay_must_not_trip`` and similar pass-by-default rules silently
mark every scenario PASS while no actual measurement happened.

Heuristic: track the last N captured ``(min, max)`` ranges per
signal. If 3 consecutive scenarios all show ``max == min == mean ==
0`` AND we're on a real-hardware backend (``hil`` / ``hybrid``), emit
a hard ERROR result + stop the run.

Disabled by default; opt in via ``THAA_LIVENESS_PROBE=on`` (or any
truthy). Tests pin it off via the autouse cleanup in ``conftest``.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


# scenario_id -> count of consecutive zero-stats observations
_FLATLINE_COUNTS: dict[str, int] = defaultdict(int)
_FLATLINE_THRESHOLD = 3


def reset() -> None:
    _FLATLINE_COUNTS.clear()


def is_enabled() -> bool:
    return (os.environ.get("THAA_LIVENESS_PROBE") or "").lower() in (
        "on", "1", "true", "yes",
    )


def is_flatlined(stats: list[dict[str, Any]]) -> bool:
    """A capture is 'flatlined' if EVERY signal reports zero range."""
    if not stats:
        return False
    for s in stats:
        for k in ("mean", "max", "min"):
            v = s.get(k)
            if isinstance(v, (int, float)) and v != 0.0:
                return False
    return True


def observe(
    backend_name: str, stats: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Update internal counters; return (should_alert, reason).

    ``backend_name``: e.g. ``"hil"``, ``"hybrid"``, ``"mock"``.
    Mock backends never trigger the alert (their stats are
    legitimately deterministic).
    """
    if not is_enabled():
        return False, ""
    if backend_name in ("mock", "xcp_mock"):
        return False, ""

    key = backend_name  # one counter per backend instance
    if is_flatlined(stats):
        _FLATLINE_COUNTS[key] += 1
    else:
        _FLATLINE_COUNTS[key] = 0

    if _FLATLINE_COUNTS[key] >= _FLATLINE_THRESHOLD:
        msg = (
            f"liveness probe: backend '{backend_name}' returned "
            f"all-zero stats for {_FLATLINE_THRESHOLD} consecutive "
            f"captures. Real hardware likely disconnected -- aborting "
            f"so subsequent scenarios don't silently PASS on stale "
            f"mock-shaped data."
        )
        return True, msg
    return False, ""
