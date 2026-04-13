"""
Node: export_tests

Writes validated test files to disk and creates a ZIP archive for download.
"""

from __future__ import annotations

import datetime
import io
import logging
import zipfile
from pathlib import Path
from typing import Any

from ..state import AgentState, make_event

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "generated_tests"


async def export_tests(state: AgentState) -> dict[str, Any]:
    """Export generated test files to disk + ZIP archive."""
    files = state.get("generated_files", {})
    validation = state.get("codegen_validation", {})
    parsed = state.get("parsed_tse", {})

    if not validation.get("valid", False):
        errors = validation.get("errors", ["Validation failed"])
        msg = f"Export skipped: {len(errors)} validation errors"
        logger.warning(msg)
        return {
            "events": [make_event("export_tests", "error", msg, {
                "errors": errors[:5],
            })],
        }

    if not files:
        return {
            "events": [make_event("export_tests", "error", "No files to export")],
        }

    topology = parsed.get("topology", "unknown") if parsed else "unknown"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dir_name = f"{topology}_{ts}"

    # Create output directory
    out_dir = OUTPUT_DIR / dir_name
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write files
    for rel_path, content in files.items():
        file_path = out_dir / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    # Create ZIP
    zip_name = f"{dir_name}.zip"
    zip_path = OUTPUT_DIR / zip_name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path, content in files.items():
            zf.writestr(f"{dir_name}/{rel_path}", content)
    zip_path.write_bytes(buf.getvalue())

    msg = f"Exported {len(files)} files to {dir_name}/ + {zip_name}"
    logger.info(msg)

    return {
        "export_path": str(out_dir),
        "events": [make_event("export_tests", "report", msg, {
            "directory": str(out_dir),
            "zip_file": zip_name,
            "file_count": len(files),
            "filenames": list(files.keys()),
        })],
    }
