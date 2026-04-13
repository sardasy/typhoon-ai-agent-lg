"""
Node: validate_code

Validates generated test code against HTAF/CLAUDE.md rules.
Checks for forbidden APIs, signal correctness, and syntax errors.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..state import AgentState, CodegenValidationResult, make_event

logger = logging.getLogger(__name__)

# Forbidden patterns in generated code
FORBIDDEN_PATTERNS = [
    (r"\bhil\.connect\s*\(", "hil.connect() is forbidden - use fixture-based setup"),
    (r'scope\s*=\s*["\']session["\']', 'scope="session" is forbidden - use scope="module"'),
    (r"\btime\.sleep\s*\(", "time.sleep() is forbidden - use ts() helper or hil.wait_msec()"),
    (r"\bhil\.start_capture\s*\(", "hil.start_capture() is legacy - use typhoon.test.capture.start_capture()"),
]

# Required patterns (at least one file must contain these)
REQUIRED_IN_TYPHOON = [
    (r"from constants import", "constants.py must be imported in test files"),
    (r"def ts\(", "ts() helper must be defined in constants.py"),
]


def _check_forbidden(filename: str, code: str) -> list[str]:
    """Check for forbidden API patterns."""
    errors = []
    for pattern, msg in FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            errors.append(f"{filename}: {msg}")
    return errors


def _check_signal_names(filename: str, code: str, parsed: dict) -> list[str]:
    """Warn if code references signals not in the parsed TSE."""
    warnings = []
    all_signals = set(parsed.get("analog_signals", []) + parsed.get("digital_signals", []))
    if not all_signals:
        return warnings

    # Find quoted strings that look like signal names in capture/set calls
    signal_refs = re.findall(r'(?:signals?\s*=\s*\[?"([^"]+)")', code)
    for sig in signal_refs:
        if sig not in all_signals and not sig.startswith("$"):
            warnings.append(f"{filename}: Signal '{sig}' not found in TSE model")
    return warnings


def _check_capture_order(filename: str, code: str) -> list[str]:
    """Ensure start_capture appears before get_capture_results."""
    errors = []
    start_pos = code.find("start_capture")
    get_pos = code.find("get_capture_results")
    if get_pos >= 0 and (start_pos < 0 or start_pos > get_pos):
        errors.append(f"{filename}: get_capture_results called before start_capture")
    return errors


def _check_syntax(filename: str, code: str) -> list[str]:
    """Syntax-check the generated code."""
    errors = []
    try:
        compile(code, filename, "exec")
    except SyntaxError as exc:
        errors.append(f"{filename}: Syntax error at line {exc.lineno}: {exc.msg}")
    return errors


def _check_ascii(filename: str, code: str) -> list[str]:
    """Ensure code is pure ASCII (CLAUDE.md requirement)."""
    errors = []
    for i, ch in enumerate(code):
        if ord(ch) > 127:
            line_no = code[:i].count("\n") + 1
            errors.append(f"{filename}: Non-ASCII character at line {line_no}")
            break
    return errors


async def validate_code(state: AgentState) -> dict[str, Any]:
    """Validate all generated test files against HTAF rules."""
    files = state.get("generated_files", {})
    parsed = state.get("parsed_tse", {})
    mode = state.get("codegen_mode", "mock")

    if not files:
        return {
            "codegen_validation": CodegenValidationResult(
                valid=False,
                errors=["No generated files to validate"],
            ).model_dump(),
            "events": [make_event("validate_code", "error", "No files to validate")],
        }

    all_errors: list[str] = []
    all_warnings: list[str] = []

    for filename, code in files.items():
        if not filename.endswith(".py"):
            continue

        all_errors.extend(_check_forbidden(filename, code))
        all_errors.extend(_check_capture_order(filename, code))
        all_errors.extend(_check_syntax(filename, code))
        all_errors.extend(_check_ascii(filename, code))
        all_warnings.extend(_check_signal_names(filename, code, parsed or {}))

    # Check required patterns in typhoon mode
    if mode == "typhoon":
        all_code = "\n".join(files.values())
        for pattern, msg in REQUIRED_IN_TYPHOON:
            if not re.search(pattern, all_code):
                all_warnings.append(msg)

    valid = len(all_errors) == 0
    result = CodegenValidationResult(
        valid=valid,
        errors=all_errors,
        warnings=all_warnings,
    )

    msg = f"Validation: {len(all_errors)} errors, {len(all_warnings)} warnings"
    if valid:
        msg = f"Validation passed ({len(all_warnings)} warnings)"
    logger.info(msg)

    return {
        "codegen_validation": result.model_dump(),
        "events": [make_event("validate_code", "observation", msg, {
            "valid": valid,
            "error_count": len(all_errors),
            "warning_count": len(all_warnings),
        })],
    }
