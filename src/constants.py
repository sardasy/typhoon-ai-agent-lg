"""Project-wide constants.

Magic numbers and string literals used across multiple modules live
here so changes happen in one place. Module-local one-off values stay
where they're used.

Conventions:
    - SHOUTING_CASE for module-level constants
    - One section per concern, with a short comment explaining the
      provenance / safety implication of the value
"""

from __future__ import annotations

from typing import Final, Literal

# ---------------------------------------------------------------------------
# Heal loop safety
# ---------------------------------------------------------------------------

# Maximum number of analyzer -> apply_fix retries on a single failing
# scenario before the orchestrator forcibly escalates. Hard cap; CLAUDE.md
# safety invariant #3.
MAX_HEAL_RETRIES: Final[int] = 3

# Minimum analyzer confidence (0..1) for ``route_after_analysis`` to take
# the retry branch. Below this, the agent escalates without writing.
ANALYZER_RETRY_MIN_CONFIDENCE: Final[float] = 0.5

# The single corrective-action type that ``apply_fix`` knows how to act
# on. Anything else routes to escalate.
ACTION_XCP_CALIBRATION: Final[str] = "xcp_calibration"


# ---------------------------------------------------------------------------
# Domains (Phase 4-B)
# ---------------------------------------------------------------------------

Domain = Literal["bms", "pcs", "grid", "general"]

# Catch-all domain. ``classify_domains`` falls back here when no
# specialty agent matches; RAG queries always include this bucket as a
# secondary so shared docs stay reachable.
DOMAIN_DEFAULT: Final[str] = "general"


# ---------------------------------------------------------------------------
# Multi-device routing (Phase 4-I)
# ---------------------------------------------------------------------------

# Default device id when a scenario / dut_config does not specify one.
# Backwards-compatible: pre-Phase-4-I scenarios all map here.
DEVICE_DEFAULT: Final[str] = "default"


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

ScenarioStatus = Literal["pass", "fail", "error", "skipped"]
TwinVerdict = Literal["commit", "veto", "uncertain"]
