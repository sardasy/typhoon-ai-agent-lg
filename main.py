"""
main.py — CLI + Web entrypoint for the LangGraph-based HIL AI Agent.

Usage:
  python main.py --goal "BMS overvoltage protection, 4.2V, 100ms"
  python main.py --server
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
except ImportError:
    pass  # python-dotenv optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("thaa")

from pydantic import BaseModel  # noqa: E402 — must be module-level for FastAPI body parsing

class RunRequest(BaseModel):
    goal: str


class CodegenRequest(BaseModel):
    tse_content: str
    tse_path: str = "uploaded.tse"
    mode: str = "mock"


# ---------------------------------------------------------------------------
# Initial state factory
# ---------------------------------------------------------------------------

def make_initial_state(goal: str, config_path: str = "configs/model.yaml") -> dict:
    """Create the initial AgentState dict for a graph run."""
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
        # HTAF codegen fields
        "tse_content": "",
        "tse_path": "",
        "parsed_tse": None,
        "test_requirements": [],
        "generated_files": {},
        "codegen_validation": None,
        "export_path": "",
        "codegen_mode": "mock",
    }


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

async def run_cli(
    goal: str,
    config_path: str,
    hitl: bool = False,
    checkpoint_db: str | None = None,
    resume_thread: str | None = None,
):
    """Stream graph execution to terminal.

    When ``hitl`` (or env ``THAA_HITL=1``) is set, the graph compiles with
    ``interrupt_before=['apply_fix']``: after the analyzer proposes an XCP
    write, the run pauses for human approval before any calibration is
    applied.

    ``checkpoint_db`` enables persistent state via SqliteSaver. When
    ``resume_thread`` is supplied, the agent resumes an existing paused
    thread (typically after a crash / restart) instead of starting a new run.
    """
    try:
        import io, sys as _sys
        from rich.console import Console
        console = Console(file=io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace"))
        HAS_RICH = True
    except ImportError:
        HAS_RICH = False
        console = None

    from src.graph import acompile_graph, compile_graph

    hitl_active = hitl or os.environ.get("THAA_HITL", "").lower() in ("1", "true", "yes")
    # Auto-enable HITL when resuming (resume only makes sense for paused threads)
    if resume_thread:
        hitl_active = True

    # Use async compile whenever we need a SQLite-backed checkpointer so the
    # AsyncSqliteSaver can open its aiosqlite connection inside our loop.
    if checkpoint_db:
        app = await acompile_graph(hitl=hitl_active, checkpoint_db=checkpoint_db)
    else:
        app = compile_graph(hitl=hitl_active)

    if resume_thread:
        thread_id = resume_thread
        initial = None  # resume from checkpoint
    else:
        initial = make_initial_state(goal, config_path)
        thread_id = f"thaa-cli-{int(datetime.datetime.now().timestamp())}"

    # When HITL is on we need a thread_id so the checkpointer can resume.
    run_config = (
        {"configurable": {"thread_id": thread_id}}
        if hitl_active else {}
    )

    banner = goal or (f"Resuming thread {thread_id}" if resume_thread else "")
    tags = []
    if hitl_active:
        tags.append("HITL")
    if checkpoint_db:
        tags.append(f"DB={checkpoint_db}")
    if resume_thread:
        tags.append("RESUME")
    tag_str = f" ({', '.join(tags)})" if tags else ""
    if HAS_RICH:
        from rich.panel import Panel
        console.print(Panel(banner, title=f"[bold]THAA{tag_str}[/bold]", border_style="blue"))
    else:
        print(f"\n=== THAA{tag_str}: {banner} ===\n")

    prev_event_count = 0
    input_value = initial

    try:
        while True:
            # astream yields {node_name: update_dict} per executed node.
            # When `interrupt_before` fires, astream simply returns (no event)
            # and snapshot.next will hold the pending node names.
            async for step_output in app.astream(input_value, config=run_config):
                for node_name, state_update in step_output.items():
                    if node_name in ("__end__", "__interrupt__"):
                        # __interrupt__ marks the pause; resolved below via snapshot.
                        continue
                    if not isinstance(state_update, dict):
                        continue
                    events = state_update.get("events", [])
                    for ev in events[prev_event_count:]:
                        _print_event(ev, console, HAS_RICH)
                    prev_event_count = 0

            if not hitl_active:
                break

            snapshot = app.get_state(run_config)
            if not snapshot.next:
                break  # graph reached END

            # We are paused before a node (default: apply_fix). Prompt the user.
            decision = _hitl_prompt(
                {
                    "next": list(snapshot.next),
                    "diagnosis": snapshot.values.get("diagnosis"),
                    "scenario": snapshot.values.get("current_scenario"),
                },
                console, HAS_RICH,
            )
            if decision == "approve":
                input_value = None  # resume
                continue
            if decision == "reject":
                # Force the analyzer's verdict to "escalate" so route_after_analysis
                # bypasses apply_fix on this attempt.
                app.update_state(run_config, {
                    "diagnosis": {"corrective_action_type": "escalate"},
                })
                input_value = None
                continue
            # abort
            if HAS_RICH:
                console.print("[red]Aborted by operator.[/red]")
            else:
                print("Aborted by operator.")
            break
    finally:
        # Release the async SQLite connection so the file lock is dropped
        # (allows --list-threads / resume from a fresh process immediately).
        saver = getattr(app, "checkpointer", None)
        conn = getattr(saver, "conn", None)
        if conn is not None and hasattr(conn, "close"):
            close_result = conn.close()
            if hasattr(close_result, "__await__"):
                try:
                    await close_result
                except Exception:
                    pass

    if HAS_RICH:
        console.print("\n[dim]Done.[/dim]\n")


def _print_event(ev: dict, console, has_rich: bool) -> None:
    etype = ev.get("event_type", "").upper()
    node = ev.get("node", "")
    msg = ev.get("message", "")
    if has_rich:
        color_map = {
            "THOUGHT": "cyan", "ACTION": "yellow",
            "OBSERVATION": "green", "RESULT": "bold green",
            "PLAN": "blue", "DIAGNOSIS": "yellow",
            "REPORT": "bold white", "ERROR": "bold red",
        }
        if "FAIL" in msg.upper():
            color_map["RESULT"] = "bold red"
        c = color_map.get(etype, "white")
        console.print(f"  [{c}][{etype}][/{c}] [dim]{node}[/dim] {msg}")
    else:
        print(f"  [{etype}] {node}: {msg}")


def _hitl_prompt(interrupt: dict, console, has_rich: bool) -> str:
    """Show the proposed action and ask the operator to approve / reject."""
    diag = interrupt.get("diagnosis") or {}
    scen = interrupt.get("scenario") or {}
    sid = scen.get("scenario_id", "?")
    action = diag.get("corrective_action_type", "?")
    param = diag.get("corrective_param", "?")
    value = diag.get("corrective_value")
    conf = diag.get("confidence", 0.0)
    desc = diag.get("root_cause_description", "")

    if has_rich:
        from rich.panel import Panel
        body = (
            f"[bold]Scenario:[/bold] {sid}\n"
            f"[bold]Root cause:[/bold] {desc}\n"
            f"[bold]Confidence:[/bold] {conf:.0%}\n"
            f"[bold]Proposed action:[/bold] [yellow]{action}[/yellow] "
            f"[white]{param}[/white] = [cyan]{value}[/cyan]"
        )
        console.print(Panel(body, title="[bold yellow]HITL approval needed[/bold yellow]",
                            border_style="yellow"))
    else:
        print("\n=== HITL APPROVAL NEEDED ===")
        print(f"Scenario       : {sid}")
        print(f"Root cause     : {desc}")
        print(f"Confidence     : {conf:.0%}")
        print(f"Proposed action: {action} {param} = {value}\n")

    while True:
        choice = input("Approve apply_fix? [y]es / [n]o (escalate) / [a]bort: ").strip().lower()
        if choice in ("y", "yes"):
            return "approve"
        if choice in ("n", "no"):
            return "reject"
        if choice in ("a", "abort", "q", "quit"):
            return "abort"



    if HAS_RICH:
        console.print("\n[dim]Done.[/dim]\n")


# ---------------------------------------------------------------------------
# Web server
# ---------------------------------------------------------------------------

def _ts_to_iso(ts: float) -> str:
    """Convert Unix timestamp float to ISO-8601 UTC string."""
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def _sse_payload(ev: dict) -> str:
    """Reshape internal event dict into the frontend-expected JSON string.

    Frontend useSSE expects: { node, message, data, timestamp (ISO) }
    Internal make_event produces: { node, event_type, message, data, timestamp (float) }
    """
    ts_raw = ev.get("timestamp", 0)
    iso_ts = _ts_to_iso(ts_raw) if ts_raw else datetime.datetime.now(datetime.timezone.utc).isoformat()
    return json.dumps(
        {
            "node":      ev.get("node", ""),
            "message":   ev.get("message", ""),
            "data":      ev.get("data") or {},
            "timestamp": iso_ts,
        },
        ensure_ascii=False,
        default=str,
    )


def run_server(config_path: str, host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    from fastapi import FastAPI, File, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse
    from sse_starlette.sse import EventSourceResponse

    app = FastAPI(title="THAA LangGraph", version="0.2.0")

    # Allow Vite dev server (5173) and any local origin to call the API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000",
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return DASHBOARD_HTML

    @app.post("/api/run")
    async def api_run(body: RunRequest):
        goal = body.goal.strip()
        if not goal:
            return JSONResponse({"error": "goal required"}, status_code=400)

        from src.graph import compile_graph

        run_id = str(uuid.uuid4())

        async def stream():
            try:
                graph_app = compile_graph()
                initial = make_initial_state(goal, config_path)

                # RunnableConfig adds run-level metadata to LangSmith traces
                from langchain_core.runnables import RunnableConfig
                run_cfg = RunnableConfig(
                    run_name=f"THAA: {goal[:60]}",
                    tags=["thaa", "hil", "verify"],
                    metadata={
                        "goal": goal,
                        "mode": "verify",
                        "thaa_run_id": run_id,
                        "config_path": config_path,
                    },
                )

                async for step in graph_app.astream(initial, config=run_cfg):
                    for node_name, update in step.items():
                        if node_name == "__end__":
                            continue
                        for ev in update.get("events", []):
                            yield {
                                "event": ev.get("event_type", "thought"),
                                "data":  _sse_payload(ev),
                            }

            except Exception as exc:
                logger.exception("Graph execution failed")
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "node":      "system",
                        "message":   str(exc),
                        "data":      {},
                        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    }),
                }

        return EventSourceResponse(stream())

    @app.get("/api/graph")
    async def get_graph_viz():
        """Return Mermaid diagram of the graph."""
        from src.graph import build_graph
        g = build_graph()
        try:
            mermaid = g.compile().get_graph().draw_mermaid()
            return {"mermaid": mermaid}
        except Exception:
            return {"mermaid": "graph LR; A-->B"}

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "framework": "langgraph"}

    # --- Report endpoints ---------------------------------------------------

    @app.get("/api/reports")
    async def list_reports():
        """List HTML reports from reports/ directory."""
        import re
        reports_dir = Path(__file__).parent / "reports"
        if not reports_dir.exists():
            return []
        result = []
        pattern = re.compile(r"^report_\d{8}_\d{6}\.html$")
        for f in sorted(reports_dir.iterdir(), reverse=True):
            if f.suffix == ".html" and pattern.match(f.name):
                # Extract timestamp from filename
                ts_part = f.name.removeprefix("report_").removesuffix(".html")
                ts_fmt = ""
                if len(ts_part) == 15:  # 20260413_144253
                    ts_fmt = f"{ts_part[:4]}-{ts_part[4:6]}-{ts_part[6:8]} {ts_part[9:11]}:{ts_part[11:13]}:{ts_part[13:15]}"
                result.append({
                    "filename": f.name,
                    "timestamp": ts_fmt,
                    "size_bytes": f.stat().st_size,
                })
        return result

    @app.get("/api/reports/{filename}")
    async def get_report(filename: str):
        """Serve a specific HTML report file."""
        import re
        from fastapi.responses import Response
        pattern = re.compile(r"^report_\d{8}_\d{6}\.html$")
        if not pattern.match(filename):
            return JSONResponse({"error": "invalid filename"}, status_code=400)
        report_path = Path(__file__).parent / "reports" / filename
        if not report_path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        html = report_path.read_text(encoding="utf-8")
        return Response(content=html, media_type="text/html")

    # --- HTAF codegen endpoints -----------------------------------------------

    from fastapi import UploadFile

    @app.post("/api/upload-tse")
    async def upload_tse(file: UploadFile):
        """Accept a .tse file upload."""
        if not file.filename.endswith(".tse"):
            return JSONResponse({"error": "Only .tse files accepted"}, status_code=400)
        content = await file.read()
        if len(content) > 10 * 1024 * 1024:  # 10MB limit
            return JSONResponse({"error": "File too large (max 10MB)"}, status_code=400)
        text = content.decode("utf-8", errors="replace")
        # Store in uploads dir
        uploads_dir = Path(__file__).parent / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        safe_name = re.sub(r"[^\w.\-]", "_", file.filename)
        (uploads_dir / safe_name).write_text(text, encoding="utf-8")
        return {
            "filename": safe_name,
            "size": len(content),
            "preview": text[:200],
        }

    @app.post("/api/generate-tests")
    async def api_generate_tests(body: CodegenRequest):
        """Run the HTAF codegen pipeline and stream events via SSE."""
        from src.graph_codegen import compile_codegen_graph

        run_id = str(uuid.uuid4())

        async def stream():
            try:
                graph_app = compile_codegen_graph()
                initial = make_initial_state("", config_path)
                initial["tse_content"] = body.tse_content
                initial["tse_path"] = body.tse_path
                initial["codegen_mode"] = body.mode

                from langchain_core.runnables import RunnableConfig
                codegen_cfg = RunnableConfig(
                    run_name=f"THAA-codegen: {body.tse_path[:60]}",
                    tags=["thaa", "codegen", body.mode],
                    metadata={
                        "tse_path": body.tse_path,
                        "mode": body.mode,
                        "thaa_run_id": run_id,
                    },
                )

                async for step in graph_app.astream(initial, config=codegen_cfg):
                    for node_name, update in step.items():
                        if node_name == "__end__":
                            continue
                        for ev in update.get("events", []):
                            yield {
                                "event": ev.get("event_type", "observation"),
                                "data":  _sse_payload(ev),
                            }
                        # Send generated files in the final step
                        if "generated_files" in update:
                            yield {
                                "event": "files",
                                "data": json.dumps({
                                    "node": "generate_tests",
                                    "message": "Generated files",
                                    "data": {"files": update["generated_files"]},
                                    "timestamp": datetime.datetime.now(
                                        datetime.timezone.utc
                                    ).isoformat(),
                                }),
                            }
            except Exception as exc:
                logger.exception("Codegen pipeline failed")
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "node": "system",
                        "message": str(exc),
                        "data": {},
                        "timestamp": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                    }),
                }

        return EventSourceResponse(stream())

    @app.get("/api/download-tests/{filename}")
    async def download_tests(filename: str):
        """Download a generated test ZIP file."""
        from fastapi.responses import FileResponse
        pattern = re.compile(r"^[\w\-]+\.zip$")
        if not pattern.match(filename):
            return JSONResponse({"error": "invalid filename"}, status_code=400)
        zip_path = Path(__file__).parent / "output" / "generated_tests" / filename
        if not zip_path.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(
            path=str(zip_path),
            media_type="application/zip",
            filename=filename,
        )

    logger.info(f"Starting THAA LangGraph dashboard on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>THAA LangGraph Dashboard</title>
<style>
  :root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;
    --pass:#22c55e;--fail:#ef4444;--warn:#f59e0b;--info:#3b82f6;--purple:#a78bfa}
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,sans-serif;background:var(--bg);color:var(--text);padding:1.5rem;max-width:900px;margin:0 auto}
  h1{font-size:1.25rem;font-weight:600;margin-bottom:.25rem}
  .sub{font-size:.8rem;color:var(--muted);margin-bottom:1rem}
  .input-row{display:flex;gap:8px;margin-bottom:1.5rem}
  input[type=text]{flex:1;padding:10px 14px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:.9rem;outline:none}
  input:focus{border-color:var(--info)}
  button{padding:10px 20px;border-radius:8px;border:none;background:var(--info);color:#fff;font-weight:600;cursor:pointer}
  button:disabled{opacity:.4;cursor:not-allowed}
  .events{display:flex;flex-direction:column;gap:3px}
  .ev{padding:8px 12px;border-radius:6px;font-size:.85rem;border-left:3px solid var(--border);background:var(--card)}
  .ev .tag{font-size:.7rem;font-weight:700;text-transform:uppercase;margin-right:6px}
  .ev .nd{color:var(--muted);font-size:.75rem;margin-right:4px}
  .ev.thought{border-left-color:var(--info)} .ev.thought .tag{color:var(--info)}
  .ev.action{border-left-color:var(--warn)} .ev.action .tag{color:var(--warn)}
  .ev.observation{border-left-color:var(--pass)} .ev.observation .tag{color:var(--pass)}
  .ev.result{border-left-color:var(--pass)} .ev.plan{border-left-color:var(--info)}
  .ev.diagnosis{border-left-color:var(--warn)} .ev.diagnosis .tag{color:var(--warn)}
  .ev.report{border-left-color:var(--purple)} .ev.report .tag{color:var(--purple)}
  .ev.error{border-left-color:var(--fail)} .ev.error .tag{color:var(--fail)}
  .ev .tag{color:var(--info)}
  .summary{margin-top:1rem;padding:12px;border-radius:8px;background:var(--card);border:1px solid var(--border);font-size:.85rem;display:none}
  .graph-badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.7rem;background:#312e81;color:#a78bfa;margin-left:8px}
</style>
</head>
<body>
<h1>Typhoon HIL AI Agent <span class="graph-badge">LangGraph</span></h1>
<p class="sub">StateGraph: load_model -> plan_tests -> execute -> analyze -> heal -> report</p>
<div class="input-row">
  <input type="text" id="goal" placeholder="Enter test goal..."
         value="BMS overvoltage protection test, 4.2V threshold, 100ms response">
  <button id="btn" onclick="run()">Run</button>
</div>
<div class="events" id="events"></div>
<div class="summary" id="summary"></div>
<script>
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function run(){
  const goal=document.getElementById('goal').value.trim();
  if(!goal)return;
  const btn=document.getElementById('btn');
  const ev=document.getElementById('events');
  const sm=document.getElementById('summary');
  btn.disabled=true; ev.innerHTML=''; sm.style.display='none';
  let r;
  try{
    r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goal})});
  }catch(e){
    ev.innerHTML='<div class="ev error"><span class="tag">ERROR</span> fetch failed: '+esc(e.message)+'</div>';
    btn.disabled=false; return;
  }
  if(!r.ok||!r.body){
    ev.innerHTML='<div class="ev error"><span class="tag">ERROR</span> HTTP '+r.status+'</div>';
    btn.disabled=false; return;
  }
  const rd=r.body.getReader(); const dec=new TextDecoder(); let buf='';
  let pendingEvent='';
  const flush=(evtType,dataLine)=>{
    if(!dataLine)return;
    let d={};
    try{d=JSON.parse(dataLine);}catch(e){return;}
    const el=document.createElement('div');
    const cls=(evtType||'thought').toLowerCase();
    el.className='ev '+cls;
    el.innerHTML='<span class="tag">'+esc(evtType||'event')+'</span><span class="nd">'+esc(d.node||'')+'</span> '+esc(d.message||'');
    ev.appendChild(el); el.scrollIntoView({behavior:'smooth',block:'nearest'});
  };
  while(true){
    const{done,value}=await rd.read(); if(done)break;
    buf+=dec.decode(value,{stream:true});
    let idx;
    while((idx=buf.indexOf('\n'))>=0){
      const line=buf.slice(0,idx).replace(/\r$/,'');
      buf=buf.slice(idx+1);
      if(line===''){continue;}
      if(line.startsWith('event:')){pendingEvent=line.slice(6).trim();continue;}
      if(line.startsWith('data:')){flush(pendingEvent,line.slice(5).trim());pendingEvent='';continue;}
    }
  }
  btn.disabled=false;
}
document.getElementById('goal').addEventListener('keydown',e=>{if(e.key==='Enter')run()});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _list_threads(checkpoint_db: str) -> int:
    """Print every thread_id stored in the SQLite checkpoint database."""
    import sqlite3
    from pathlib import Path
    p = Path(checkpoint_db).expanduser()
    if not p.is_file():
        print(f"[thaa] checkpoint db not found: {p}")
        return 1
    conn = sqlite3.connect(str(p))
    try:
        cur = conn.execute(
            "SELECT thread_id, MAX(checkpoint_id) AS last_ckpt, COUNT(*) AS n "
            "FROM checkpoints GROUP BY thread_id ORDER BY last_ckpt DESC"
        )
        rows = list(cur.fetchall())
    except sqlite3.OperationalError as exc:
        print(f"[thaa] cannot read {p}: {exc}")
        return 1
    finally:
        conn.close()

    if not rows:
        print(f"[thaa] no threads in {p}")
        return 0
    print(f"[thaa] {len(rows)} thread(s) in {p}:\n")
    print(f"  {'THREAD_ID':45s}  {'CHECKPOINTS':>11s}  LAST_CHECKPOINT_ID")
    for thread_id, last_ckpt, n in rows:
        print(f"  {thread_id:45s}  {n:>11d}  {last_ckpt}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="THAA — LangGraph HIL Agent")
    parser.add_argument("--goal", type=str, help="Test goal (NL)")
    parser.add_argument("--server", action="store_true", help="Web dashboard")
    parser.add_argument("--config", type=str, default="configs/model.yaml")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--hitl", action="store_true",
                        help="Human-in-the-loop: pause before apply_fix for approval")
    parser.add_argument("--checkpoint-db", type=str,
                        help="Path to SQLite checkpoint DB (persists HITL state across restarts)")
    parser.add_argument("--resume-thread", type=str,
                        help="Resume an existing paused thread by ID (requires --checkpoint-db)")
    parser.add_argument("--list-threads", action="store_true",
                        help="List all thread IDs in the checkpoint DB and exit")
    args = parser.parse_args()

    if args.list_threads:
        db = args.checkpoint_db or os.environ.get("THAA_CHECKPOINT_DB")
        if not db:
            print("--list-threads requires --checkpoint-db or THAA_CHECKPOINT_DB")
            sys.exit(1)
        sys.exit(_list_threads(db))

    if args.server:
        run_server(args.config, args.host, args.port)
    elif args.resume_thread or args.goal:
        asyncio.run(run_cli(
            args.goal or "",
            args.config,
            hitl=args.hitl,
            checkpoint_db=args.checkpoint_db,
            resume_thread=args.resume_thread,
        ))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
