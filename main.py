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
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
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
    }


# ---------------------------------------------------------------------------
# CLI mode
# ---------------------------------------------------------------------------

async def run_cli(goal: str, config_path: str):
    """Stream graph execution to terminal."""
    try:
        from rich.console import Console
        console = Console()
        HAS_RICH = True
    except ImportError:
        HAS_RICH = False
        console = None

    from src.graph import compile_graph

    app = compile_graph()
    initial = make_initial_state(goal, config_path)

    if HAS_RICH:
        from rich.panel import Panel
        console.print(Panel(goal, title="[bold]Test Goal[/bold]", border_style="blue"))
    else:
        print(f"\n=== Test Goal: {goal} ===\n")

    prev_event_count = 0

    # Stream through graph nodes
    async for step_output in app.astream(initial):
        # step_output is {node_name: state_update}
        for node_name, state_update in step_output.items():
            if node_name == "__end__":
                continue

            events = state_update.get("events", [])
            for ev in events[prev_event_count:]:
                etype = ev.get("event_type", "").upper()
                node = ev.get("node", "")
                msg = ev.get("message", "")

                if HAS_RICH:
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

            prev_event_count = 0  # events list is appended fresh per step

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
    from fastapi import FastAPI
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

        async def stream():
            try:
                graph_app = compile_graph()
                initial = make_initial_state(goal, config_path)

                # RunnableConfig adds run-level metadata to LangSmith traces
                from langchain_core.runnables import RunnableConfig
                run_cfg = RunnableConfig(
                    run_name=f"THAA: {goal[:60]}",
                    tags=["thaa", "hil"],
                    metadata={"goal": goal},
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
async function run(){
  const goal=document.getElementById('goal').value.trim();
  if(!goal)return;
  const btn=document.getElementById('btn');
  const ev=document.getElementById('events');
  const sm=document.getElementById('summary');
  btn.disabled=true; ev.innerHTML=''; sm.style.display='none';
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goal})});
  const rd=r.body.getReader(); const dec=new TextDecoder(); let buf='';
  while(true){
    const{done,value}=await rd.read(); if(done)break;
    buf+=dec.decode(value,{stream:true});
    const lines=buf.split('\\n'); buf=lines.pop();
    for(const l of lines){
      if(!l.startsWith('data:'))continue;
      try{
        const d=JSON.parse(l.slice(5));
        const el=document.createElement('div');
        el.className='ev '+(d.event_type||'');
        el.innerHTML='<span class="tag">'+(d.event_type||'')+'</span><span class="nd">'+(d.node||'')+'</span> '+(d.message||'');
        ev.appendChild(el); el.scrollIntoView({behavior:'smooth'});
      }catch(e){}
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

def main():
    parser = argparse.ArgumentParser(description="THAA — LangGraph HIL Agent")
    parser.add_argument("--goal", type=str, help="Test goal (NL)")
    parser.add_argument("--server", action="store_true", help="Web dashboard")
    parser.add_argument("--config", type=str, default="configs/model.yaml")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.server:
        run_server(args.config, args.host, args.port)
    elif args.goal:
        asyncio.run(run_cli(args.goal, args.config))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
