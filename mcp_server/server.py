"""THAA MCP server -- expose the LangGraph verification pipeline as MCP tools.

Run with::

    python -m mcp_server.server          # stdio transport (Claude Desktop default)
    python -m mcp_server.server --http   # streamable HTTP on :8765

The tool surface is intentionally coarse: each MCP call corresponds to
one verification pipeline run (or one HITL step), not one HIL primitive.
This keeps the LLM orchestration layer simple and lets the existing
LangGraph self-healing loop own the fine-grained decisions.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any, Literal

import yaml

# Make the project root importable when this file is run as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:
    raise SystemExit(
        "The 'mcp' package is required. Install with:\n"
        "    pip install 'mcp[cli]>=1.0'\n"
    ) from exc

from src.graph import acompile_graph  # noqa: E402  -- after sys.path tweak
from src.graph_codegen import compile_codegen_graph  # noqa: E402
from src.nodes.apply_fix import get_xcp  # noqa: E402
from src.nodes.load_model import get_hil, get_rag  # noqa: E402
from src.tools.xcp_tools import LAST_XCP_WRITE  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] mcp.thaa: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mcp.thaa")


# ---------------------------------------------------------------------------
# Initial state factory (kept here so the server has no dependency on main.py)
# ---------------------------------------------------------------------------

def _make_initial_state(goal: str, config_path: str) -> dict[str, Any]:
    return {
        "goal": goal,
        "config_path": config_path,
        "model_path": "",
        "model_signals": [],
        "model_loaded": False,
        "device_mode": "",
        "active_preset": "",
        "rag_context": "",
        "plan_strategy": "",
        "scenarios": [],
        "scenario_index": 0,
        "estimated_duration_s": 0,
        "standard_coverage": {},
        "results": [],
        "current_scenario": None,
        "diagnosis": None,
        "heal_retry_count": 0,
        "events": [],
        "report_path": "",
        "error": "",
        "tse_content": "",
        "tse_path": "",
        "parsed_tse": None,
        "test_requirements": [],
        "generated_files": {},
        "codegen_validation": None,
        "export_path": "",
        "codegen_mode": "mock",
    }


def _summarize_result(state: dict[str, Any]) -> dict[str, Any]:
    """Reduce a LangGraph terminal state into a small JSON-friendly summary."""
    results = state.get("results") or []
    summary = {
        "scenarios_total": len(results),
        "scenarios_passed": sum(1 for r in results if (r.get("status") if isinstance(r, dict) else getattr(r, "status", "")) == "passed"),
        "scenarios_failed": sum(1 for r in results if (r.get("status") if isinstance(r, dict) else getattr(r, "status", "")) == "failed"),
        "report_path": state.get("report_path", ""),
        "error": state.get("error", ""),
        "events_count": len(state.get("events") or []),
    }
    summary["scenarios"] = []
    for r in results:
        d = r if isinstance(r, dict) else (r.model_dump() if hasattr(r, "model_dump") else {})
        summary["scenarios"].append({
            "scenario_id": d.get("scenario_id"),
            "status": d.get("status"),
            "fault_template": d.get("fault_template"),
            "notes": d.get("notes", ""),
        })
    return summary


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("thaa-verification")


@mcp.tool()
def list_scenario_libraries() -> list[dict[str, Any]]:
    """List bundled YAML scenario libraries under ``configs/``.

    Returns one entry per file with topology, scenario count, and any
    declared standards coverage. Use this to pick a ``config_path`` for
    ``run_verification`` or ``start_hitl_run``.
    """
    out = []
    for yml in sorted((_ROOT / "configs").glob("scenarios*.yaml")):
        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            out.append({"path": str(yml.relative_to(_ROOT)), "error": str(exc)})
            continue
        scenarios = data.get("scenarios") or {}
        # YAML uses dict keyed by scenario_id, but tolerate list shape too.
        if isinstance(scenarios, dict):
            scenario_ids = list(scenarios.keys())
            count = len(scenarios)
        elif isinstance(scenarios, list):
            scenario_ids = [s.get("scenario_id", "") for s in scenarios if isinstance(s, dict)]
            count = len(scenarios)
        else:
            scenario_ids, count = [], 0
        out.append({
            "path": str(yml.relative_to(_ROOT)),
            "topology": data.get("topology", ""),
            "description": data.get("description", ""),
            "scenarios_count": count,
            "standards": data.get("standards", []),
            "scenario_ids": scenario_ids[:25],
        })
    return out


@mcp.tool()
async def run_verification(
    goal: str,
    config_path: str = "configs/model.yaml",
) -> dict[str, Any]:
    """Run the full THAA verification pipeline end-to-end (no HITL pauses).

    The graph executes load_model -> plan_tests -> execute -> [analyze ->
    apply_fix loop] -> generate_report and returns a summary of scenario
    results plus the path of the generated HTML report.

    Args:
        goal: Natural-language verification goal, e.g.
            "BMS overvoltage protection at 4.2V with 100ms response".
        config_path: Path (relative to project root) to a YAML config.
            Use ``list_scenario_libraries`` to discover available bundles.
    """
    log.info("run_verification: goal=%s config=%s", goal, config_path)
    app = await acompile_graph(hitl=False)
    initial = _make_initial_state(goal, config_path)
    final_state = await app.ainvoke(initial)
    return _summarize_result(final_state)


@mcp.tool()
async def start_hitl_run(
    goal: str,
    checkpoint_db: str,
    config_path: str = "configs/model.yaml",
) -> dict[str, Any]:
    """Start a HITL verification run; pause at the first proposed apply_fix.

    Returns the ``thread_id`` plus the analyzer's proposed corrective
    action so the caller (LLM or operator UI) can inspect before
    approving. Resume with ``resume_hitl_run``.

    Requires a SQLite checkpoint DB so state survives across MCP calls
    (each call is a separate request).
    """
    log.info("start_hitl_run: goal=%s db=%s", goal, checkpoint_db)
    app = await acompile_graph(hitl=True, checkpoint_db=checkpoint_db)
    thread_id = f"thaa-mcp-{uuid.uuid4().hex[:12]}"
    cfg = {"configurable": {"thread_id": thread_id}}
    initial = _make_initial_state(goal, config_path)

    try:
        async for _ in app.astream(initial, config=cfg):
            pass  # checkpointer captures every step; we just drain
        snapshot = await app.aget_state(cfg)
    finally:
        await _close_checkpointer(app)

    paused_at = list(snapshot.next) if snapshot.next else []
    diag = snapshot.values.get("diagnosis") if snapshot.values else None
    return {
        "thread_id": thread_id,
        "checkpoint_db": checkpoint_db,
        "paused_before": paused_at,
        "is_paused": bool(paused_at),
        "diagnosis": diag,
        "current_scenario": (snapshot.values or {}).get("current_scenario"),
        "summary": _summarize_result(snapshot.values or {}),
    }


@mcp.tool()
async def resume_hitl_run(
    thread_id: str,
    decision: Literal["approve", "reject"],
    checkpoint_db: str,
) -> dict[str, Any]:
    """Resume a paused HITL thread.

    ``approve`` proceeds with apply_fix as proposed by the analyzer.
    ``reject`` overrides the diagnosis to ``escalate``, skipping the
    XCP write and advancing past this scenario.

    Returns the snapshot after the next pause or after END.
    """
    log.info("resume_hitl_run: thread=%s decision=%s", thread_id, decision)
    app = await acompile_graph(hitl=True, checkpoint_db=checkpoint_db)
    cfg = {"configurable": {"thread_id": thread_id}}
    try:
        if decision == "reject":
            await app.aupdate_state(
                cfg, {"diagnosis": {"corrective_action_type": "escalate"}}
            )
        async for _ in app.astream(None, config=cfg):
            pass
        snapshot = await app.aget_state(cfg)
    finally:
        await _close_checkpointer(app)

    paused_at = list(snapshot.next) if snapshot.next else []
    return {
        "thread_id": thread_id,
        "paused_before": paused_at,
        "is_paused": bool(paused_at),
        "is_complete": not paused_at,
        "diagnosis": (snapshot.values or {}).get("diagnosis"),
        "summary": _summarize_result(snapshot.values or {}),
    }


@mcp.tool()
async def generate_pytest_from_tse(
    tse_path: str | None = None,
    tse_content: str | None = None,
    mode: Literal["mock", "typhoon"] = "mock",
) -> dict[str, Any]:
    """Generate a pytest test suite from a Typhoon HIL .tse model file.

    Runs the HTAF codegen pipeline (parse_tse -> map_requirements ->
    generate_tests -> validate_code -> export_tests) and returns a
    summary of the parsed model, requirement count, generated files,
    validation result, and the export ZIP path.

    Provide exactly one of ``tse_path`` (resolved relative to the project
    root if not absolute) or ``tse_content`` (raw .tse text). ``mode``
    selects ``mock`` (default, no Typhoon API needed) or ``typhoon``
    (requires Typhoon HIL API in the environment).
    """
    if not tse_path and not tse_content:
        return {"error": "Provide either tse_path or tse_content."}
    if tse_path and tse_content:
        return {"error": "Provide only one of tse_path or tse_content."}

    if tse_path:
        p = Path(tse_path)
        if not p.is_absolute():
            p = _ROOT / p
        if not p.is_file():
            return {"error": f"tse_path not found: {p}"}
        content = p.read_text(encoding="utf-8", errors="replace")
        original_path = str(p.name)
    else:
        content = tse_content or ""
        original_path = "uploaded.tse"

    log.info(
        "generate_pytest_from_tse: path=%s mode=%s bytes=%d",
        original_path, mode, len(content),
    )

    initial = _make_initial_state("", "configs/model.yaml")
    initial["tse_content"] = content
    initial["tse_path"] = original_path
    initial["codegen_mode"] = mode

    app = compile_codegen_graph()
    final = await app.ainvoke(initial)

    files = final.get("generated_files") or {}
    validation = final.get("codegen_validation") or {}
    parsed = final.get("parsed_tse") or {}
    requirements = final.get("test_requirements") or []

    return {
        "tse_path": original_path,
        "mode": mode,
        "topology": parsed.get("topology", ""),
        "model_name": parsed.get("model_name", ""),
        "signal_count": (
            len(parsed.get("analog_signals", []))
            + len(parsed.get("digital_signals", []))
        ),
        "requirements_count": len(requirements),
        "generated_files": [
            {"path": rel, "size_bytes": len(code)}
            for rel, code in files.items()
        ],
        "validation": {
            "valid": validation.get("valid", False),
            "errors": validation.get("errors", []),
            "warnings": validation.get("warnings", []),
        },
        "export_path": final.get("export_path", ""),
        "error": final.get("error", ""),
    }


@mcp.tool()
def list_threads(checkpoint_db: str) -> list[dict[str, Any]]:
    """List thread IDs in a SQLite checkpoint DB and how many checkpoints each has.

    Use the returned ``thread_id`` with ``resume_hitl_run`` to continue a
    paused run after a process restart.
    """
    p = Path(checkpoint_db).expanduser()
    if not p.is_file():
        return []
    conn = sqlite3.connect(str(p))
    try:
        cur = conn.execute(
            "SELECT thread_id, MAX(checkpoint_id) AS last_ckpt, COUNT(*) AS n "
            "FROM checkpoints GROUP BY thread_id ORDER BY last_ckpt DESC"
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError as exc:
        return [{"error": str(exc)}]
    finally:
        conn.close()
    return [
        {"thread_id": tid, "last_checkpoint_id": last, "checkpoint_count": n}
        for tid, last, n in rows
    ]


# ---------------------------------------------------------------------------
# Read-only introspection tools (no state changes, safe to call any time)
# ---------------------------------------------------------------------------

_REPORT_PATTERN = re.compile(r"^report_\d{8}_\d{6}\.html$")


@mcp.tool()
async def rag_query(
    query: str,
    sources: list[str] | None = None,
    top_k: int = 5,
) -> dict[str, Any]:
    """Search the THAA knowledge base for relevant documents.

    Wraps ``RAGToolExecutor`` (ChromaDB primary, Qdrant fallback, mock KB
    last). Useful for pulling standards excerpts, API docs, or past test
    history into a planning prompt without invoking the full
    verification pipeline.

    Args:
        query: Search string.
        sources: Subset of ``["api_docs", "standards", "test_history",
            "datasheets"]``. Defaults to all except ``datasheets``.
        top_k: Maximum results to return (default 5).
    """
    if sources is None:
        sources = ["api_docs", "standards", "test_history"]
    rag = get_rag()
    result = await rag.execute(
        "rag_query", {"query": query, "sources": sources, "top_k": top_k}
    )
    # Normalize the shape so the caller always sees {query, results: [...]}.
    if "results" not in result:
        result = {"query": query, "results": [], "raw": result}
    return result


@mcp.tool()
def list_reports() -> list[dict[str, Any]]:
    """List HTML verification reports under ``reports/``.

    Returns most recent first. Each entry has ``filename``, ``timestamp``
    (formatted from the filename), and ``size_bytes``. Use the filename
    with ``get_report`` to fetch the HTML body.
    """
    reports_dir = _ROOT / "reports"
    if not reports_dir.is_dir():
        return []
    out = []
    for f in sorted(reports_dir.iterdir(), reverse=True):
        if f.suffix != ".html" or not _REPORT_PATTERN.match(f.name):
            continue
        ts_part = f.name.removeprefix("report_").removesuffix(".html")
        ts_fmt = ""
        if len(ts_part) == 15:
            ts_fmt = (
                f"{ts_part[:4]}-{ts_part[4:6]}-{ts_part[6:8]} "
                f"{ts_part[9:11]}:{ts_part[11:13]}:{ts_part[13:15]}"
            )
        out.append({
            "filename": f.name,
            "timestamp": ts_fmt,
            "size_bytes": f.stat().st_size,
        })
    return out


@mcp.tool()
def get_report(filename: str, max_bytes: int = 200_000) -> dict[str, Any]:
    """Fetch the HTML body of a specific report file.

    The filename must match ``report_YYYYMMDD_HHMMSS.html``. Files
    larger than ``max_bytes`` are truncated and ``truncated=True`` is
    flagged so the caller knows to fetch the remainder via filesystem
    if needed.
    """
    if not _REPORT_PATTERN.match(filename):
        return {"error": f"Invalid report filename: {filename!r}"}
    path = _ROOT / "reports" / filename
    if not path.is_file():
        return {"error": f"Report not found: {filename}"}
    raw = path.read_bytes()
    truncated = len(raw) > max_bytes
    body = raw[:max_bytes].decode("utf-8", errors="replace")
    return {
        "filename": filename,
        "size_bytes": len(raw),
        "truncated": truncated,
        "html": body,
    }


@mcp.tool()
async def hil_status() -> dict[str, Any]:
    """Read-only snapshot of the HIL device executor state.

    Reports whether a model is loaded, whether simulation is running,
    the model path, the discovered signal count, and the first 30
    signal names. Reads the same singleton ``HILToolExecutor`` that
    the running graph uses, so the answer reflects current runtime
    state -- not just default values.
    """
    hil = get_hil()
    base = await hil.execute("hil_control", {"action": "status"})
    return {
        **base,
        "signal_count": len(hil.signals),
        "signal_names_preview": hil.signals[:30],
        "snapshot_count": len(hil.snapshots),
    }


@mcp.tool()
async def xcp_read_params(
    param_names: list[str],
    a2l_path: str | None = None,
) -> dict[str, Any]:
    """Read one or more XCP parameters from the connected ECU.

    Strictly read-only -- writes are blocked by the existing whitelist
    in ``XCPToolExecutor`` and are not exposed via MCP at all. If the
    executor is not yet connected and ``a2l_path`` is provided, this
    tool will connect first; otherwise it returns the connection error
    along with whatever was last written by ``apply_fix`` (so the
    caller still gets useful state).

    Returns a dict with ``values`` (name -> read result) and
    ``last_writes`` (recent writes by the heal loop, for debugging).
    """
    xcp = get_xcp()
    connect_error: str | None = None

    if not xcp.connected:
        if a2l_path:
            conn_result = await xcp.execute(
                "xcp_interface", {"action": "connect", "a2l_path": a2l_path}
            )
            if conn_result.get("error"):
                connect_error = conn_result["error"]
        else:
            connect_error = (
                "Not connected to XCP. Pass a2l_path to auto-connect, "
                "or call connect via the verification pipeline first."
            )

    values: dict[str, Any] = {}
    if not connect_error:
        for name in param_names:
            r = await xcp.execute(
                "xcp_interface", {"action": "read", "variable": name}
            )
            values[name] = r.get("value") if "value" in r else r

    return {
        "connected": xcp.connected,
        "a2l_path": xcp.a2l_path,
        "values": values,
        "last_writes": dict(LAST_XCP_WRITE),
        "connect_error": connect_error,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _close_checkpointer(app: Any) -> None:
    """Release the AsyncSqliteSaver connection so the file lock drops."""
    saver = getattr(app, "checkpointer", None)
    conn = getattr(saver, "conn", None)
    if conn is None:
        return
    close = getattr(conn, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        try:
            await result
        except Exception:  # noqa: BLE001 -- best-effort cleanup
            log.warning("checkpointer close failed", exc_info=True)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="THAA MCP server")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Use streamable-HTTP transport instead of stdio (default port 8765).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.http:
        log.info("Starting THAA MCP server on http://%s:%d", args.host, args.port)
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        log.info("Starting THAA MCP server on stdio")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
