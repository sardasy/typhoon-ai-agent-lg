# THAA MCP Server (PoC)

Wraps the THAA LangGraph verification pipeline as a Model Context Protocol
(MCP) server. Any MCP-compatible host -- Claude Desktop, a custom
orchestrator, or another LLM agent -- can drive HIL verification by
calling tools, instead of importing THAA as a library.

This is a **proof-of-concept scaffold** for the 4-layer MCP architecture
discussion: it validates that THAA can be exposed as a single coarse
"verification pipeline" tool while preserving the existing self-healing
loop and HITL approval flow.

## Tool surface

| Tool | Purpose |
|------|---------|
| `list_scenario_libraries()` | Inventory of `configs/scenarios*.yaml` (topology, count, standards) |
| `run_verification(goal, config_path?)` | Run end-to-end without pauses; returns scenario summary |
| `start_hitl_run(goal, checkpoint_db, config_path?)` | Run until first `apply_fix` pause; returns `thread_id` + diagnosis |
| `resume_hitl_run(thread_id, decision, checkpoint_db)` | Resume a paused thread (`approve` / `reject`) |
| `list_threads(checkpoint_db)` | List paused threads in a SQLite checkpoint DB |
| `generate_pytest_from_tse(tse_path?, tse_content?, mode?)` | HTAF codegen: .tse model -> pytest suite (returns generated file list + ZIP path) |

The whole self-healing loop (analyze -> apply_fix -> retry, MAX_HEAL_RETRIES,
safety validator) lives inside `run_verification` / `start_hitl_run`; the
MCP host does **not** orchestrate individual HIL primitives. This is the
intended design -- the LLM picks *which* verification job to run, the
LangGraph state machine handles *how*.

## Install

```bash
pip install 'mcp[cli]>=1.0'
# (rest of THAA's requirements should already be installed)
```

## Run

```bash
# stdio transport (Claude Desktop default)
python -m mcp_server.server

# streamable HTTP transport for remote hosts
python -m mcp_server.server --http --port 8765
```

## Claude Desktop configuration

Add to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/`, Windows:
`%APPDATA%\Claude\`):

```json
{
  "mcpServers": {
    "thaa-verification": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "C:/Users/a/Downloads/hilagent/typhoon_ai_agent_lg",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "PYTHONPATH": "C:/Users/a/Downloads/hilagent/typhoon_ai_agent_lg"
      }
    }
  }
}
```

Then in Claude Desktop, ask things like:

- "List the scenario libraries available."
- "Run BMS overvoltage verification at 4.2V, 100ms response, using configs/scenarios.yaml."
- "Start a HITL run for grid-forming inverter verification and pause for my approval."
- "Generate pytest tests from `examples/dab.tse` in mock mode and tell me what was produced."

## HITL flow over MCP

MCP tool calls are JSON-RPC -- each call is independent. To keep state
across approval steps, HITL runs **must** be backed by the SQLite
checkpointer:

```
1. start_hitl_run(goal=..., checkpoint_db="thaa.db")
   -> { thread_id: "thaa-mcp-abc123", paused_before: ["apply_fix"], diagnosis: {...} }

2. (LLM/operator inspects the diagnosis)

3. resume_hitl_run(thread_id="thaa-mcp-abc123", decision="approve", checkpoint_db="thaa.db")
   -> { is_complete: false, paused_before: ["apply_fix"], ... }   # next pause
   or
   -> { is_complete: true, summary: { scenarios_passed: 5, ... } }  # done
```

Each tool call opens an `AsyncSqliteSaver`, drains the next chunk of the
graph, and closes the connection so the lock is released for the next
call.

## Known limitations of this PoC

- **Long blocking calls**: `run_verification` synchronously runs the
  whole pipeline (could be minutes). MCP supports progress notifications,
  but this scaffold does not emit them yet -- callers see no events
  until the run completes.
- **No auth / multi-tenant story**: the server runs with the host
  process's credentials. For LG enterprise use, add a transport-layer
  auth proxy (or move to streamable-HTTP behind SSO).
- **Single HIL device assumption**: tool executor singletons
  (`get_hil()`, `get_xcp()`, `get_rag()`) are module-level. Concurrent
  MCP calls from different threads will share the same HIL connection.
  Run only one verification at a time on a real device.
- **Codegen result is summarized**: `generate_pytest_from_tse` returns
  the *list* of generated files and their sizes, not their contents
  (a single .tse can produce dozens of files). The export ZIP path is
  returned so the caller can fetch the bytes via filesystem access or
  the existing FastAPI download endpoint. If you need the full file
  contents over MCP, add an MCP `resource` for each generated file.

## How this fits the 4-layer MCP plan

```
+-------------------------------------------+
| Layer 4: User interface (Claude Desktop,  |
|          web chat, IDE plugin)            |
+-------------------------------------------+
                  | MCP over stdio / HTTP
+-------------------------------------------+
| Layer 3: Agent Orchestrator (LLM picks    |
|          tools, manages multi-turn ctx,   |
|          safety gates)                    |
+-------------------------------------------+
                  | MCP tool calls
+-------------------------------------------+
| Layer 2: MCP servers (this scaffold +     |
|          schematic-model-builder Skill,   |
|          tse-to-pytest Skill, RAG, ...)   |
+-------------------------------------------+
                  | Python imports
+-------------------------------------------+
| Layer 1: Capabilities (THAA LangGraph,    |
|          HTAF, RAG store, dual-DUT,       |
|          fault templates, ...)            |
+-------------------------------------------+
```

This server is one Layer-2 component. Adding the other Skills and HTAF
endpoints follows the same pattern: wrap the existing capability with a
small `FastMCP` adapter, no rewrite required.
