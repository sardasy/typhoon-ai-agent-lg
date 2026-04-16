"""
Node: validate_code

Validates generated test code against HTAF/CLAUDE.md rules.
Checks for forbidden APIs, signal correctness, and syntax errors.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..state import AgentState, CodegenValidationResult, make_event
from ..tools import get_hil_api_docs, get_pytest_api, parse_ini_markers

logger = logging.getLogger(__name__)


def _load_codegen_config() -> dict[str, Any]:
    """Load configs/codegen.yaml (optional)."""
    cfg_path = Path(__file__).resolve().parents[2] / "configs" / "codegen.yaml"
    if not cfg_path.is_file():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    try:
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}

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


def _check_hil_api_calls(filename: str, code: str, api_docs) -> list[str]:
    """Flag hil.X(...) calls whose X is not present in the parsed hil_api.html.

    Returns a list of human-readable messages. Callers decide whether to
    classify them as errors or warnings based on unknown_call_severity.
    """
    if not api_docs.is_loaded():
        return []
    unknown = api_docs.unknown_calls(code)
    return [
        f"{filename}: hil.{name}(...) is not in hil_api.html API index"
        for name in unknown
    ]


def _check_pytest_markers(
    filename: str,
    code: str,
    pytest_api,
    declared: set[str],
) -> list[str]:
    """Flag @pytest.mark.<name> markers not in builtins and not in pytest.ini."""
    if not pytest_api.is_loaded():
        return []
    unknown = pytest_api.unknown_markers(code, declared=declared)
    return [
        f"{filename}: @pytest.mark.{name} is not a builtin marker "
        f"and not declared in pytest.ini"
        for name in unknown
    ]


def _check_pytest_attrs(filename: str, code: str, pytest_api) -> list[str]:
    """Flag pytest.X references where X is not in pytest's public API."""
    if not pytest_api.is_loaded():
        return []
    unknown = pytest_api.unknown_pytest_attrs(code)
    return [
        f"{filename}: pytest.{name} is not in the installed pytest public API"
        for name in unknown
    ]


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

    # Load HIL API docs + pytest introspection + config-driven severities.
    codegen_cfg = _load_codegen_config()
    doc_cfg = codegen_cfg.get("hil_api_docs", {}) or {}
    pyt_cfg = codegen_cfg.get("pytest_api", {}) or {}

    api_docs = get_hil_api_docs(doc_cfg.get("path"))
    pytest_api = get_pytest_api()

    hil_severity = str(doc_cfg.get("unknown_call_severity", "warning")).lower()
    marker_severity = str(pyt_cfg.get("unknown_marker_severity", "warning")).lower()
    attr_severity = str(pyt_cfg.get("unknown_attr_severity", "warning")).lower()

    # Custom markers declared in any generated pytest.ini avoid false positives.
    declared_markers: set[str] = set()
    for fname, content in files.items():
        if fname.endswith("pytest.ini"):
            declared_markers.update(parse_ini_markers(content))

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

        hil_msgs = _check_hil_api_calls(filename, code, api_docs)
        if hil_severity == "error":
            all_errors.extend(hil_msgs)
        else:
            all_warnings.extend(hil_msgs)

        marker_msgs = _check_pytest_markers(filename, code, pytest_api, declared_markers)
        if marker_severity == "error":
            all_errors.extend(marker_msgs)
        else:
            all_warnings.extend(marker_msgs)

        attr_msgs = _check_pytest_attrs(filename, code, pytest_api)
        if attr_severity == "error":
            all_errors.extend(attr_msgs)
        else:
            all_warnings.extend(attr_msgs)

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
    refs = []
    if api_docs.is_loaded():
        refs.append(f"HIL API: {api_docs.count()} members")
    if pytest_api.is_loaded():
        refs.append(f"pytest v{pytest_api.version()}")
    if refs:
        msg += " [" + "; ".join(refs) + "]"
    logger.info(msg)

    return {
        "codegen_validation": result.model_dump(),
        "events": [make_event("validate_code", "observation", msg, {
            "valid": valid,
            "error_count": len(all_errors),
            "warning_count": len(all_warnings),
            "hil_api_members": api_docs.count() if api_docs.is_loaded() else 0,
            "hil_api_doc_path": api_docs._loaded_path or "",
            "pytest_version": pytest_api.version() if pytest_api.is_loaded() else "",
            "pytest_public_symbols": (
                len(pytest_api.list_public_api()) if pytest_api.is_loaded() else 0
            ),
            "declared_markers": sorted(declared_markers),
        })],
    }
