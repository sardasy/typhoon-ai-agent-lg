"""
Validator -- Safety guard for all agent tool actions.

Every tool call passes through here before execution.
Blocks dangerous actions and enforces physical limits.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SafetyConfig:
    max_voltage: float = 60.0
    max_current: float = 200.0
    max_fault_injections: int = 10
    auto_retry_limit: int = 3
    timeout_per_test_s: float = 30.0
    writable_xcp_params: set[str] = field(default_factory=lambda: {
        "BMS_scanInterval_ch1", "BMS_scanInterval_ch2",
        "BMS_scanInterval_ch3", "BMS_scanInterval_ch4",
        "BMS_scanInterval_ch5", "BMS_scanInterval_ch6",
        "BMS_scanInterval_ch7", "BMS_scanInterval_ch8",
        "BMS_scanInterval_ch9", "BMS_scanInterval_ch10",
        "BMS_scanInterval_ch11", "BMS_scanInterval_ch12",
        "Ctrl_Kp", "Ctrl_Ki", "Ctrl_Kd",
        # VSM (IEEE 2800 GFM) tuning -- safe to retune via heal loop
        "J", "D", "Kv",
    })


@dataclass
class ValidationResult:
    allowed: bool
    reason: str = ""
    modified_input: dict | None = None  # clamped values


class Validator:
    """Pre-flight check for every tool invocation."""

    def __init__(self, config: SafetyConfig | None = None):
        self.config = config or SafetyConfig()
        self._fault_count = 0
        self._retry_counts: dict[str, int] = {}

    def validate(self, tool_name: str, tool_input: dict) -> ValidationResult:
        """Check if a tool call is safe to execute."""

        # --- Voltage / current limits ---
        for key in ("value", "end_value", "fault_voltage"):
            if key in tool_input:
                v = tool_input[key]
                if isinstance(v, (int, float)) and abs(v) > self.config.max_voltage:
                    return ValidationResult(
                        allowed=False,
                        reason=f"Voltage {v}V exceeds safety limit {self.config.max_voltage}V",
                    )

        # --- Fault injection limit ---
        if tool_name == "hil_fault_inject":
            self._fault_count += 1
            if self._fault_count > self.config.max_fault_injections:
                return ValidationResult(
                    allowed=False,
                    reason=f"Fault injection limit reached ({self.config.max_fault_injections})",
                )

        # --- XCP write whitelist ---
        if tool_name == "xcp_interface" and tool_input.get("action") == "write":
            var = tool_input.get("variable", "")
            if var not in self.config.writable_xcp_params:
                return ValidationResult(
                    allowed=False,
                    reason=f"XCP write to '{var}' is not whitelisted. Escalate to human.",
                )

        # --- Model modification guard ---
        if tool_name == "hil_control" and tool_input.get("action") == "modify_model":
            return ValidationResult(
                allowed=False,
                reason="Plant model modification is forbidden. Only DUT parameters may be changed.",
            )

        return ValidationResult(allowed=True)

    def check_retry_limit(self, scenario_id: str) -> bool:
        """Returns True if retry is allowed, False if limit exceeded."""
        count = self._retry_counts.get(scenario_id, 0)
        if count >= self.config.auto_retry_limit:
            return False
        self._retry_counts[scenario_id] = count + 1
        return True

    def reset_fault_count(self):
        self._fault_count = 0

    def reset_retry_counts(self):
        self._retry_counts.clear()
