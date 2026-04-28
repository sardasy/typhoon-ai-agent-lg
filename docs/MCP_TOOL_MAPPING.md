# THAA Asset → MCP Tool Mapping

**Purpose.** One-page decision document for the 4-layer MCP architecture
(see `mcp_server/README.md`). For every existing THAA asset, decides
**(a)** what its MCP tool surface should be, **(b)** whether it is already
exposed, and **(c)** the integration cost.

The default rule of thumb: **prefer few coarse tools over many fine
ones.** Stateful primitives stay inside LangGraph; the MCP host picks
*which* job to run, not *how*.

---

## Status legend

- :white_check_mark: **Exposed** -- tool already in `mcp_server/server.py`
- :small_blue_diamond: **Cheap add** -- under a day, no code outside `mcp_server/`
- :hammer_and_wrench: **Adapter needed** -- new code in `src/` or external repo
- :no_entry: **Don't expose** -- intentionally encapsulated; routed via existing tool

---

## 1. Skills (external)

| Skill | Capability today | MCP target | Status | Notes |
|---|---|---|---|---|
| `schematic-model-builder` | TyphoonAPI SchematicAPI: build `.tse` schematic from natural-language model spec | `build_schematic(spec, output_path)` -> `.tse` path + summary | :hammer_and_wrench: External repo (not in THAA) | Add a separate FastMCP adapter in the Skill repo. THAA's MCP host can chain `build_schematic -> generate_pytest_from_tse -> run_verification`. |
| `tse-to-pytest` | Parse `.tse`, map requirements, generate pytest suite | `generate_pytest_from_tse(tse_path?, tse_content?, mode?)` | :white_check_mark: **Already exposed** (via `graph_codegen` pipeline) | The Skill version and the THAA codegen pipeline are functionally equivalent; current PoC uses the THAA in-repo path. Decide later whether to retire the Skill or keep it as a lightweight stand-alone. |

> **Decision.** Don't duplicate `tse-to-pytest` -- the THAA codegen graph is
> already the source of truth. `schematic-model-builder` stays in its own
> repo and exposes itself as a separate MCP server; both servers are
> registered in the same MCP host so the LLM can chain them.

---

## 2. HTAF (FastAPI backend, `main.py`)

| Endpoint | Body / Param | MCP target | Status |
|---|---|---|---|
| `POST /api/run` | `RunRequest{goal}` | `run_verification(goal, config_path?)` | :white_check_mark: |
| `POST /api/generate-tests` | `CodegenRequest{tse_content, tse_path, mode}` | `generate_pytest_from_tse(...)` | :white_check_mark: |
| `POST /api/upload-tse` | multipart `.tse` | (subsumed -- pass `tse_content` arg directly) | :no_entry: |
| `GET  /api/reports` | -- | `list_reports()` | :white_check_mark: |
| `GET  /api/reports/{filename}` | filename | `get_report(filename, max_bytes?)` -> HTML body + truncation flag | :white_check_mark: |
| `GET  /api/download-tests/{filename}` | filename | `download_test_zip(filename)` -> bytes (base64), OR MCP `resource://generated_tests/{filename}` | :small_blue_diamond: |
| `GET  /api/graph` | -- | `get_graph_diagram()` -> Mermaid string | :small_blue_diamond: |
| `GET  /api/health` | -- | (skip -- MCP `initialize` already covers this) | :no_entry: |

> **Decision.** `list_reports` + `get_report` (or as MCP **resources**) are
> the most useful next adds -- the LLM frequently wants to summarize
> "what did the last 5 runs find?" Idiomatic MCP would expose reports as
> resources (URIs the host can fetch) rather than tool calls. Pick one,
> not both.

---

## 3. RAG (`src/tools/rag_tools.py`)

| Capability today | MCP target | Status |
|---|---|---|
| `RAGToolExecutor.execute("rag_query", {query, sources, top_k})` | `rag_query(query, sources?, top_k?)` -> `{query, results: [{text, source, score}, ...]}` | :white_check_mark: |

**Backends present in this repo:**
- ChromaDB (primary, `chroma_db/` PersistentClient)
- Qdrant (HTTP fallback)
- Mock KB (hardcoded IEC 62619 / Typhoon API / BMS history snippets)
- Embedder: `BAAI/bge-m3` via sentence-transformers (Qdrant path only)

**Caveats.**
- Indexing script `scripts/index_knowledge.py` is referenced but absent on
  this branch; the Chroma collection is empty until populated. The mock
  KB fallback is what the LLM actually sees today.
- Exposing `rag_query` over MCP is safe (read-only, no side effects, no
  hardware) and immediately useful: the orchestrator LLM can pull
  context without going through the verification pipeline.

> **Decision.** Cheapest high-value next add. One tool, no state, no auth
> issues. Add it together with `list_reports`.

---

## 4. Dual-path DUT (`hil_tools.py`, `xcp_tools.py`)

| Primitive today | Why **not** to expose as MCP | Already inside which MCP tool |
|---|---|---|
| `HILToolExecutor._control` (load/start/stop) | Stateful single-threaded device | `run_verification`, `start_hitl_run` |
| `_signal_write` (constant/ramp/sine) | Sequence ordering matters; must be inside scenario | same |
| `_signal_read` | OK to expose for debug; minor value alone | same |
| `_capture` (start/stop, statistics) | Tied to a running scenario | same |
| `_fault_inject` | Safety-critical; needs validator gate | same |
| `XCPToolExecutor` (read/write calibration) | Whitelist-gated; gate must run on every call | `apply_fix` (inside `run_verification`) |

**Recommended (small) read-only carve-out:**

| Tool | Use case | Status |
|---|---|---|
| `hil_status()` -> `{model_loaded, simulation_running, signal_count, signal_names_preview, snapshot_count}` | "Is the HIL ready?" | :white_check_mark: |
| `xcp_read_params(param_names, a2l_path?)` -> `{values, last_writes, connected, connect_error}` | Inspect ECU calibration without writing; also exposes recent `apply_fix` writes for debugging | :white_check_mark: read-only, no whitelist gate needed |

**SCPI / TCP.** Not present in this repo. The Typhoon HIL API
(`set_scada_input_value`, `set_source_*`, `read_analog_signal`)
abstracts the device transport. If LG's lab needs raw SCPI for
external instruments (e.g. Chroma sources, Yokogawa scopes), that is a
**new asset**, not a wrap of existing code.

> **Decision.** Do **not** expose write primitives as MCP tools. Two
> read-only tools (`hil_status`, `xcp_read_params`) are enough to
> answer 80% of "what's the system doing right now" questions and cost
> almost nothing.

---

## 5. Self-healing pipeline

| Component | File | Already exposed via |
|---|---|---|
| `MAX_HEAL_RETRIES = 3` | `src/graph.py` | inside `run_verification` |
| `analyze_failure` Claude call | `src/nodes/analyze_failure.py` | inside `run_verification` |
| `apply_fix` XCP write + validator | `src/nodes/apply_fix.py` | inside `run_verification` |
| HITL pause `interrupt_before=['apply_fix']` | `src/graph.py` | `start_hitl_run` / `resume_hitl_run` |
| SqliteSaver checkpointer | `src/graph.py::acompile_graph` | every HITL tool call |

> **Decision.** Already covered. The Jenkins layer that exists on the
> `claude/upbeat-bell-9d77c7` branch (Jenkinsfile, JobDSL seed, n8n
> webhooks) is a deployment concern -- it lives **above** MCP, calling
> the same `run_verification` tool from a CI runner. No new MCP surface
> needed.

---

## Priority queue (next 4 cheap adds) -- DONE

All 4 read-only adds shipped together with the original PoC:

1. :white_check_mark: **`rag_query`** -- orchestrator pulls KB context without invoking the pipeline
2. :white_check_mark: **`list_reports` + `get_report`** -- summarize past runs without re-running
3. :white_check_mark: **`hil_status`** -- "Is the HIL ready?" without a full run
4. :white_check_mark: **`xcp_read_params`** -- debug calibration; **writes intentionally not exposed**

Tool count: **6 -> 11**. Read-only surface coverage of the asset list:
complete.

## Don't expose (and why)

- **HIL write primitives** (`signal_write`, `fault_inject`) -- safety-critical
  ordering, must stay inside scenarios
- **XCP writes** (`apply_fix`) -- already gated by validator + HITL
- **`/api/upload-tse`** -- redundant with `tse_content` arg
- **`/api/health`** -- MCP `initialize` already does this

## External (separate MCP server, not this repo)

- `schematic-model-builder` -- ships with the Skill repo
- (future) raw-SCPI / TCP instrument adapter -- new asset, not a wrap

---

## Appendix: today's MCP tool surface (11 tools)

| Tool | Notes |
|---|---|
| `list_scenario_libraries()` | `configs/scenarios*.yaml` inventory |
| `run_verification(goal, config_path?)` | wraps `acompile_graph` |
| `start_hitl_run(goal, checkpoint_db, config_path?)` | wraps `acompile_graph(hitl=True)` |
| `resume_hitl_run(thread_id, decision, checkpoint_db)` | uses `app.aupdate_state` |
| `list_threads(checkpoint_db)` | reads SqliteSaver DB directly |
| `generate_pytest_from_tse(tse_path?, tse_content?, mode?)` | wraps `compile_codegen_graph` |
| `rag_query(query, sources?, top_k?)` | wraps `RAGToolExecutor.execute("rag_query", ...)` |
| `list_reports()` | scans `reports/`; matches `report_YYYYMMDD_HHMMSS.html` |
| `get_report(filename, max_bytes?)` | reads HTML body; truncates large files with flag |
| `hil_status()` | calls `hil_control` action=status, plus signal preview |
| `xcp_read_params(param_names, a2l_path?)` | calls `xcp_interface` action=read; auto-connect option; exposes `LAST_XCP_WRITE` |
