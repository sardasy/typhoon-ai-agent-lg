# CLAUDE.md -- Typhoon HIL AI Agent (LangGraph Edition)

> **Owner:** 미림씨스콘 (Milim Syscon) -- Typhoon HIL Korea solution engineering.
> **Goal:** dual-path verification (**VHIL ↔ HIL**) with one pytest asset.
> See section 11 below for the Mirim Syscon Hard Rules (ASCII-only,
> timedelta indexing, MODEL_PATH, etc.) that all generated code MUST honor.

## Project identity

This is **THAA** (Typhoon HIL AI Agent) -- a LangGraph-based AI agent system that
automates **controller verification** using Typhoon HIL (Hardware-in-the-Loop)
simulation equipment.

The core problem it solves: power electronics engineers spend hours manually writing
test scripts, running them, interpreting failures, and tuning parameters. This system
replaces that workflow with an AI agent that takes a natural-language test goal
(e.g. "verify BMS overvoltage protection at 4.2V with 100ms response"), then
autonomously plans test scenarios, executes them on HIL hardware, diagnoses failures
by reading ECU internals via XCP protocol, applies calibration fixes, retests, and
generates a complete verification report.

Target verification domains: BMS protection logic, ESS/PCS inverter control,
battery formation equipment, motor drive controllers, DC-DC converter regulation.

## Development environment

This project is designed to be developed and extended using **Claude Code** (CLI).
All source files, configs, and prompts are structured for AI-assisted development:

- `CLAUDE.md` (this file) provides project context and rules
- `prompts/*.md` contain agent system prompts (editable independently from code)
- `configs/*.yaml` define test scenarios and hardware config (no code changes needed)
- Mock mode allows full development without physical HIL hardware
- All 381 tests pass without any external dependencies (no API key, no HIL, no XCP)

## Architecture summary

```
main.py (CLI/FastAPI)
  └─ graph.py  ← LangGraph StateGraph (the control plane)
       ├─ nodes/load_model.py      → HIL API + Qdrant RAG
       ├─ nodes/plan_tests.py      → Claude Planner (NL → JSON)
       ├─ nodes/execute_scenario.py → HIL stimulus + capture + pass/fail
       ├─ nodes/analyze_failure.py  → Claude Analyzer (diagnosis JSON)
       ├─ nodes/apply_fix.py       → pyXCP calibration write
       ├─ nodes/advance_scenario.py → scenario pointer ++
       └─ nodes/generate_report.py → Jinja2 HTML report
```

The graph has 3 conditional edges: `route_after_exec`, `route_after_analysis`,
`route_has_more`. The self-healing loop is: execute → fail → analyze → apply_fix → execute.

## Commands

```bash
# Run tests (381 tests, should all pass — run this after every change)
cd typhoon_ai_agent_lg && python -m pytest tests/ -v

# Run a single test goal (requires ANTHROPIC_API_KEY)
python main.py --goal "BMS overvoltage protection, 4.2V, 100ms"

# Launch web dashboard
python main.py --server --port 8000

# Type check (uses mypy.ini)
python -m mypy

# One-shot quality gate (pytest --cov + mypy)
scripts\check.bat
```

See `docs/QUALITY.md` for the coverage / mypy strictness policy.

### Claude Code workflow

When working on this project with Claude Code, follow this sequence:

1. **Read before writing.** Always read the relevant node file and `state.py`
   before modifying any node. Understand what state fields the node reads/writes.
2. **Run tests after every change.** `python -m pytest tests/ -v` must stay at
   381 passed (or more if you added tests). Never commit with failing tests.
3. **Add a routing test for every new conditional edge.** If you add a new branch
   in `route_after_exec`, add a test case for it in `TestRouteAfterExec`.
4. **Mock mode first.** Develop and test with `HAS_TYPHOON=False`. Only test on
   real hardware after all mock tests pass.
5. **One node per file.** Each LangGraph node lives in its own file under
   `src/nodes/`. Don't combine multiple nodes in one file.

## Code conventions

### Language and encoding
- All Python source code MUST be pure ASCII. No Korean characters in code, comments,
  or string literals inside `.py` files. Korean is allowed only in: prompts/*.md,
  configs/*.yaml description fields, and CLAUDE.md.
- Reason: Typhoon HIL TyphoonTest IDE chokes on non-ASCII Python files.

### Python style
- Python 3.12+. Use `from __future__ import annotations` in every file.
- Type hints everywhere. Use `X | None` not `Optional[X]`.
- Pydantic v2 models for structured data. Use `model_dump()` not `.dict()`.
- `dataclass` for internal config objects, Pydantic for serialized data.
- Async by default for all node functions and tool executors.
- No bare `except:`. Always catch specific exceptions.

### LangGraph patterns
- Every node signature: `async def node_name(state: AgentState) -> dict[str, Any]`
- Nodes return partial state updates only — never return the full state.
- Use `Annotated[list, operator.add]` for append-only fields (results, events).
- Conditional edge functions are pure (no side effects, no I/O): `def route(state) -> Literal[...]`
- Keep routing logic in `graph.py`, node logic in `nodes/*.py`. Never put routing in nodes.

### State discipline
- `AgentState` is the single source of truth. No module-level mutable globals
  except tool executor singletons (HILToolExecutor, XCPToolExecutor, RAGToolExecutor).
- Tool executors are singletons accessed via `get_hil()`, `get_xcp()`, `get_rag()`.
- Every node MUST append at least one event to `events[]` via `make_event()`.
- Never mutate `scenarios[]` — it's set once by `plan_tests` and read-only after that.
- `scenario_index` is the only mutable pointer into `scenarios[]`.

### Testing
- All routing logic must have unit tests (test every branch of every conditional edge).
- Graph structure tests: verify node presence, entry point, terminal edges.
- Tool executors must work in mock mode (HAS_TYPHOON=False, HAS_XCP=False).
- Use `pytest.ini` with `asyncio_mode = auto`.
- Test file naming: `test_*.py` in `tests/`.

## Domain-critical rules

### Safety invariants (NEVER violate these)
1. **Never modify plant model parameters.** The Agent controls the DUT (device under test)
   only. Plant model = physical system simulation. DUT = the controller being tested.
   Modifying plant parameters invalidates the test.
2. **XCP write whitelist is mandatory.** `validator.py` contains `writable_xcp_params`.
   Any XCP write to a parameter not in this set MUST be blocked. Safety-critical
   parameters (OVP/UVP/OCP thresholds, protection enables) require human approval.
3. **Max 3 heal retries per scenario.** `MAX_HEAL_RETRIES = 3` in `graph.py`.
   After 3 failed retries, the Agent MUST escalate (advance to next scenario),
   never loop infinitely.
4. **Voltage/current limits are hard caps.** `SafetyConfig.max_voltage` and
   `max_current` are enforced by the Validator before every tool call. These
   represent physical equipment limits, not software preferences.

### Fault template library (src/fault_templates.py)
10 stimulus templates available, dispatched by `parameters.fault_template`:

| Template | Use case | Standards |
|----------|----------|-----------|
| `overvoltage` / `undervoltage` | Cell or AC OVP/UVP ramp | IEC 62619, IEEE 1547 |
| `voltage_sag` / `voltage_swell` | LVRT / HVRT (3-phase) | IEEE 1547 §6.4 |
| `frequency_deviation` | OF/UF trip + freq excursion | IEEE 1547 §6.5 |
| `short_circuit` / `open_circuit` | Switch fault injection | IEC 62619 |
| `vsm_steady_state` | VSM Pref/Qref/J/D/Kv driver | IEEE 2800 §9 |
| `vsm_pref_step` | Inertia response (J sweep) | IEEE 2800 §7.2.2 |
| `phase_jump` | Phase angle step (≤25°) | IEEE 2800 §7.3 |

All templates support 3-phase via `signal_ac_sources: ["Vsa","Vsb","Vsc"]`.
IEEE 1547/2800 bounds are enforced inside each template (raises ValueError).

### Standards coverage (current)
Predefined scenario libraries by domain:

| YAML file | Topology | Scenarios | Standards |
|-----------|----------|-----------|-----------|
| `configs/scenarios.yaml` | BMS 12S pack | 10 | IEC 62619, IEEE 1547 |
| `configs/scenarios_250123.yaml` | ESS/EV charger | 32 | IEEE 1547, IEC 62619/61851, UL 9540 |
| `configs/scenarios_vsm_gfm.yaml` | VSM GFM inverter | 23 | IEEE 2800-2022 (6 sections) |

Predefined scenarios in YAML are loaded directly (no Claude Planner call) by
`plan_tests.py::_load_predefined_scenarios()`. Falls back to Claude when the
config has no `scenarios:` section.

### Typhoon HIL API rules
- `typhoon.api.hil` is local-only and single-threaded. Never call from multiple
  async tasks concurrently — use `asyncio.Lock` if needed.
- Always `load_model` before `start_simulation`. Always `stop_simulation` in
  `generate_report` (the terminal node).
- Use `typhoon.test.capture.start_capture` and `get_capture_results` — not
  the legacy `hil.start_capture` (which doesn't exist in newer API versions).
- Signal names must match the model exactly (case-sensitive).
  Validate against `model_signals` from the `load_model` step.

### pyXCP rules
- A2L file MUST match the ECU firmware version. Mismatched A2L = silent wrong data.
- Always validate XCP read values against expected ranges before using them
  for diagnosis.
- CAN bus bandwidth: XCP DAQ at >50Hz can interfere with BMS operational
  CAN messages. Reduce DAQ rate during time-critical protection tests.

### Claude API usage
- Use `claude-sonnet-4-20250514` for Planner and Analyzer calls (best speed/quality
  tradeoff for structured JSON output).
- Always set `temperature=0` for deterministic-ish output.
- Planner and Analyzer prompts live in `prompts/*.md`. The system prompt instructs
  Claude to return ONLY JSON with no markdown fences.
- Always strip ```` ```json ```` and ```` ``` ```` from Claude responses before parsing.
- Handle `json.JSONDecodeError` gracefully — set error state, don't crash the graph.

## File responsibilities

| File | Owns | Depends on |
|------|------|------------|
| `state.py` | AgentState TypedDict, Pydantic models, make_event() | nothing |
| `graph.py` | Single-agent StateGraph topology, conditional edge functions, MAX_HEAL_RETRIES | state, all nodes |
| `graph_orchestrator.py` | Phase 4-B/D/F multi-agent StateGraph + per-domain marker nodes + parallel fan-out | graph, domain_classifier, parallel_agents |
| `parallel_agents.py` | Phase 4-F per-domain async workers (Send-driven) | nodes/* |
| `domain_classifier.py` | Heuristic scenario domain classifier + per-agent prompt overlays | nothing |
| `twin.py` | Phase 4-C digital twin: calibration mirror + what-if predictor | nothing |
| `nodes/simulate_fix.py` | Twin-gated apply_fix vetoer (opt-in via `--twin`) | twin |
| `audit.py` | HITL approval audit trail (JSONL, regulator-grade) | nothing |
| `constants.py` | Project-wide constants (heal retries, domain labels, action types) | nothing |
| `nodes/load_model.py` | HIL model loading, signal discovery, RAG context fetch | tools/hil, tools/rag |
| `nodes/plan_tests.py` | Claude Planner call, JSON plan parsing | langchain-anthropic |
| `nodes/execute_scenario.py` | Stimulus application, waveform capture, pass/fail evaluation | tools/dut |
| `nodes/analyze_failure.py` | Claude Analyzer call, diagnosis JSON parsing | langchain-anthropic |
| `nodes/apply_fix.py` | XCP calibration write, safety validation | tools/dut, validator |
| `nodes/advance_scenario.py` | scenario_index increment, state cleanup | nothing |
| `nodes/generate_report.py` | Simulation stop, Jinja2 HTML rendering | tools/hil, reporter |
| `tools/dut/` | DUT abstraction (HIL/XCP/Hybrid/Mock backends, Phase 4) | tools/hil, tools/xcp |
| `tools/hil_tools.py` | Typhoon HIL API wrapper (5 tools) | typhoon.api.hil (optional) |
| `tools/xcp_tools.py` | pyXCP wrapper + writable param whitelist | pyxcp (optional) |
| `tools/rag_tools.py` | Qdrant vector search + mock KB | qdrant-client (optional) |
| `validator.py` | Safety guard: voltage/current limits, XCP whitelist, fault count | nothing |
| `reporter.py` | Jinja2 HTML/Xray report generation | jinja2 |
| `main.py` | CLI runner, FastAPI+SSE web dashboard, initial state factory | graph |

## Adding new features

### Adding a new node
1. Create `src/nodes/your_node.py` with `async def your_node(state: AgentState) -> dict`.
2. Add any new state fields to `AgentState` in `state.py`.
3. Wire it in `graph.py`: `graph.add_node(...)` + edges/conditional edges.
4. Add routing tests in `tests/test_graph.py`.
5. Make sure the node appends to `events[]`.

### Adding a new tool
1. Add tool JSON schema + executor method in `src/tools/`.
2. Register in `src/tools/__init__.py`.
3. If the tool writes to hardware, add safety checks in `validator.py`.
4. Add mock mode (tool must work without the hardware library installed).

### Adding human-in-the-loop
```python
# In graph.py:
from langgraph.checkpoint.memory import MemorySaver
app = build_graph().compile(
    checkpointer=MemorySaver(),
    interrupt_before=["apply_fix"],  # pause before XCP write
)
```

## Common Claude Code tasks

These are the types of requests you will receive most often. Follow the patterns below.

### "Add a new test scenario type"
1. Add scenario definition in `configs/scenarios.yaml`
2. Add stimulus logic in `execute_scenario.py` `_apply_stimulus()` (new `elif` branch)
3. Add pass/fail rule evaluation in `_evaluate()` (new `elif` for the rule key)
4. Test with mock: the scenario should produce a `ScenarioResult` with correct status

### "Add a new tool the agent can use"
1. Define tool JSON schema in `src/tools/your_tools.py`
2. Implement executor class with mock fallback
3. Add to `ALL_TOOLS` in `src/tools/__init__.py`
4. If the tool can cause harm (writes to hardware), add a check in `validator.py`

### "Make the agent handle a new failure type"
1. The Analyzer prompt (`prompts/analyzer.md`) may need a new root_cause category
2. If the fix requires a new corrective_action_type beyond `xcp_calibration`,
   add handling in `apply_fix.py` and update `route_after_analysis` in `graph.py`
3. Add routing tests for the new branch

### "Add a new state field"
1. Add the field to `AgentState` in `state.py` with appropriate type
2. If it's append-only, use `Annotated[list[X], operator.add]`
3. Set a default value in `make_initial_state()` in `main.py`
4. Document which nodes read/write it (add a comment in `state.py`)

### "Change the graph topology"
1. Modify `build_graph()` in `graph.py`
2. Update conditional edge functions if routing logic changes
3. Update `TestGraphStructure` to verify new edges/nodes
4. Update the ASCII topology comment at the top of `graph.py`

## Known gotchas

1. **LangGraph `Annotated[list, operator.add]` fields** — if a node returns
   `{"results": single_dict}` instead of `{"results": [single_dict]}`, LangGraph
   will crash. Always wrap in a list.

2. **Tool executor singletons** — `_hil`, `_xcp`, `_rag` are module-level.
   If you run multiple graph instances in the same process, they share the same
   HIL connection. This is intentional (one device) but be aware for testing.

3. **pytest collection warnings** — Pydantic models starting with "Test" (e.g.
   `TestResult`) trigger pytest collection. We renamed to `ScenarioResult` to avoid
   this. Don't create Pydantic models with names starting with "Test".

4. **LangGraph graph introspection** — `get_graph().nodes` returns a dict (keys are
   node ID strings), not a list of objects. Use `set(graph.nodes)` not
   `{n.id for n in graph.nodes}`.

5. **Claude JSON parsing** — Claude sometimes wraps JSON in markdown fences even
   when told not to. Always strip with `.removeprefix("```json").removesuffix("```")`.

6. **asyncio.sleep in execute_scenario** — needed for HIL stimulus settling time.
   In mock mode this adds real delay. For fast tests, mock `asyncio.sleep` or
   reduce durations in test scenario parameters.

## Environment variables

```
ANTHROPIC_API_KEY=sk-ant-...      # Required for Claude API calls
QDRANT_URL=http://localhost:6333  # Optional, for RAG

# Production controls (P0 + P1 sprint)
THAA_VHIL=1                       # Force VHIL simulator over physical HIL
THAA_VHIL_DEVICE=HIL606           # Override VHIL device class
THAA_MAX_CLAUDE_CALLS_PER_RUN=200 # Hard cap on analyze_failure calls
THAA_DIAGNOSIS_CACHE=off          # Disable on-disk diagnosis cache
THAA_DIAGNOSIS_CACHE_PATH=runs/diag_cache.jsonl
THAA_HEARTBEAT_PATH=runs/heartbeat.json   # Liveness file (watchdog target)
THAA_HITL_TIMEOUT=600             # Auto-reject HITL after N seconds
THAA_LIVENESS_PROBE=on            # Abort on 3 consecutive flatline captures
THAA_AUDIT_PATH=runs/hitl_audit.jsonl
THAA_AUDIT_OPERATOR=operator@org.com
THAA_AUDIT_ROTATE=off             # Disable date-based rotation


# LangSmith tracing (all optional, opt-in)
LANGCHAIN_TRACING_V2=true         # Enables tracing when set to "true"
LANGCHAIN_API_KEY=lsv2_pt_...     # Required if tracing enabled
LANGCHAIN_PROJECT=thaa-dev        # Optional, defaults to "default"
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com  # Optional, self-host override
```

### Data handling (LangSmith)

When `LANGCHAIN_TRACING_V2=true`, full prompts (including `goal`,
`model_signals`, RAG context) and Claude responses are sent to LangSmith
(api.smith.langchain.com, US region by default). Do not enable tracing for
runs containing proprietary model data or customer PII unless your LangSmith
workspace is approved for it. Set `LANGCHAIN_ENDPOINT` to a self-hosted
instance if needed.

Runs are tagged with `thaa`, `hil`, and `verify` or `codegen`. Each run
carries a `thaa_run_id` UUID in metadata for correlation with backend logs.
Node-level LLM spans (`plan_tests.llm`, `analyze_failure.llm`) are tagged
with the node name and model ID for filtering.

## Project roadmap

- **Phase 1 (MVP):** load_model -> plan_tests -> execute -> report. No healing loop.
  Sufficient for basic automated controller verification.
- **Phase 2 (current):** Full graph with analyze_failure + apply_fix self-healing loop.
  Agent can diagnose and fix calibration issues autonomously.
- **Phase 3 (planned):** RAG knowledge base population (IEC/UL standards, API docs,
  past test history), multi-device support, LangSmith tracing, PostgreSQL
  checkpointer for crash recovery.
- **Phase 4-A:** DUT abstraction MVP. `execute_scenario` and
  `apply_fix` route through `DUTBackend` (`src/tools/dut/`) — HIL,
  XCP, Hybrid, Mock backends selectable via `--dut-backend` /
  `state["dut_backend"]`. Same scenario YAML runs against HIL model or
  real ECU. See `docs/DUT_ABSTRACTION.md`.
- **Phase 4-B:** Multi-agent orchestration. Scenarios are
  auto-classified into BMS / PCS / Grid / General domains by
  `src/domain_classifier.py`; `src/graph_orchestrator.py` builds a
  StateGraph with per-domain marker nodes and a per-agent analyzer
  prompt overlay. Activated with `--orchestrator`. See
  `docs/MULTI_AGENT.md`.
- **Phase 4-C:** Digital twin MVP. `src/twin.py` mirrors
  the ECU's calibration state and gates `apply_fix` via a
  `simulate_fix` node — vetoes no-op / out-of-range /
  wrong-direction writes before they hit hardware. Activated with
  `--twin` (composes with `--orchestrator`). See
  `docs/DIGITAL_TWIN.md`.
- **Phase 4-D:** Orchestrator now supports `--hitl` +
  `--checkpoint-db` (sync `SqliteSaver` and async `AsyncSqliteSaver`)
  via `compile_orchestrator_graph` / `acompile_orchestrator_graph` —
  mirrors the single-agent pair. Multi-agent runs can pause before
  `apply_fix` and resume after process restart, same UX as
  Phase 3 HITL.
- **Phase 4-E:** `XCPBackend.capture()` now performs
  pyxcp DAQ-based waveform capture (real path) with a mock fallback
  that mirrors HIL mock's heal-target convergence. `--dut-backend xcp`
  self-heal demos converge without an ECU. See `docs/DUT_ABSTRACTION.md`.
- **Phase 4-F:** Parallel domain agents via LangGraph
  `Send`. `compile_parallel_orchestrator_graph()` fans out non-empty
  domains to concurrent async workers (`src/parallel_agents.py`);
  each worker runs the full heal loop on its scenario subset.
  Hardware contention serialized by
  `src/tools/dut/base.py::HARDWARE_LOCK` (asyncio.Lock); Claude
  analyzer calls overlap. Activated with `--orchestrator --parallel`.
  See `docs/MULTI_AGENT.md`.
- **Phase 4-G:** Per-domain RAG namespaces. The RAG tool
  accepts an optional `domain` filter (Chroma `where`, Qdrant
  payload predicate, mock metadata vote); the indexer tags every
  document via `infer_doc_domain` heuristic. `load_model` populates
  `state["rag_context_by_domain"]` with one bucket per domain;
  `analyze_failure` reads the matching bucket per scenario. Always
  includes `general` as a catch-all. See `docs/RAG_INDEXING.md`.
- **Phase 4-H:** Real HIL404 + real ECU bring-up
  tooling: `scripts/preflight.py` (env / config / HIL / XCP / RAG /
  twin checks), `--preflight` / `--preflight-strict` CLI flags,
  `scripts/run_smoke_real.bat`, expanded `docs/REAL_TYPHOON_BRINGUP.md`
  with 7-step bring-up checklist + Phase 4 ramp recommendations +
  troubleshooting matrix.
- **Phase 4-I (current, stable):** Multi-device HIL. Per-device backend
  cache + per-device `asyncio.Lock` (`get_hardware_lock(device_id)`).
  Scenarios opt in via a YAML `device_id` field;
  `state["device_pool"]` carries per-device config overlays. Same
  device serializes; different devices overlap. Backward-compatible
  via `"default"` device id. See `docs/REAL_TYPHOON_BRINGUP.md` and
  `docs/DUT_ABSTRACTION.md`.
- **Phase 4-J:** HITL inside the parallel orchestrator. Parallel
  workers run in **defer-heals mode** when `state["hitl_active"]`:
  diagnose via Claude in parallel, append `(scenario, diagnosis)` to
  `pending_fixes` (operator.add reducer). Parent's serial replay loop
  (`next_pending_fix` -> interrupt-before `approve_fix` -> `apply_fix`
  -> `execute_scenario` -> route_has_pending) drains the queue with
  per-fix operator approval. SQLite checkpointing supported via
  `acompile_parallel_orchestrator_graph`. See `docs/MULTI_AGENT.md`.

---

## 11. Mirim Syscon Hard Rules (NEVER violate)

These come from the team CLAUDE.md (operator-side context). Each
rule is enforced by an automated test in ``tests/`` -- a violation
trips CI before the change can land.

### 11.1 ASCII-only Python source
TyphoonTest IDE on Windows reads ``.py`` files as cp1254 and crashes
on multi-byte input. **No Korean / em-dash / smart quotes / ellipsis
in any ``src/*.py`` or ``scripts/*.py``**. Allowed in ``.md``,
``.yaml``, ``prompts/`` only. Enforced by
``tests/test_ascii_only.py`` (parametrised over every shipped
source file).

### 11.2 capture results use ``pd.Timedelta`` indexing
``typhoon.test.capture`` returns a Timedelta-indexed DataFrame.
Integer / float ``.iloc`` / ``.loc`` calls silently return wrong
rows. **Use ``src.timedelta_helpers.at(df, t_seconds)`` /
``between(df, start, stop)``** instead of indexing by hand.

### 11.3 MODEL_PATH is absolute, resolved by pytest rootpath
Never use ``os.getcwd()`` or relative paths inside test code. The
shared session-scoped fixture in ``tests/conftest.py``:

```python
@pytest.fixture(scope="session")
def model_path(pytestconfig) -> Path: ...
```

Override per run via ``MODEL_PATH=...`` or ``DUT_MODEL=<name>``
env vars.

### 11.4 Pre-built schematic blocks priority
SchematicAPI generation MUST use ``core/Boost``, ``core/Half Bridge``,
``core/Full Bridge``, etc. when the topology is known.
Don't reconstruct from discrete IGBT + diode + inductor.

### 11.5 ``typhoon.test.*`` high-level API only
Tests call ``typhoon.test.capture`` / ``typhoon.test.signals`` /
``typhoon.test.ranges`` / ``typhoon.test.reporting``. Direct calls
to ``typhoon.api.hil`` belong inside ``src/tools/dut/`` -- never
in ``tests/``.

### 11.6 Tag / Goto / From for long-distance routing
SchematicAPI builders MUST use Tag connections for long traces.
Direct wires across the schematic break the auto-generator.

### 11.7 Banned terms in test IDs
``conftest.py::pytest_collection_modifyitems`` exits the run on any
test ID containing the banned set. Currently: ``"InterBattery"``.

## 12. DUT_MODE single switch

```bash
DUT_MODE=vhil pytest tests/unit/        # VHIL simulator path
DUT_MODE=xcp pytest tests/integration/  # real ECU path
```

Reads as alias of ``--dut-backend`` (vhil -> hil internally).
Test code uses the ``dut_mode`` session fixture rather than the env
var directly.

## 13. Marker registry

Registered in ``conftest.py::pytest_configure``:

| Marker | Semantics |
|--------|-----------|
| ``vhil_only`` | VHIL-only -- skipped on real HIL |
| ``hw_required`` | real ECU/HIL required -- skipped on mock |
| ``fault_injection`` | Roadmap P1 scenarios (``src/fault_harness.py``) |
| ``regression`` | CI gate -- run on every PR |
| ``comm_protocol`` | Roadmap P2 (Modbus/CAN) |
| ``hil_measurement`` | Roadmap P3 (SignalAnalyzer) |

## 14. Mirim Syscon roadmap (priorities)

| # | Item | Status |
|---|------|--------|
| P1 | VHIL fault injection harness | **scaffolded** -- ``src/fault_harness.py`` (ECU-side primitives + ``FaultScenario`` API + 3 canonical examples) |
| P2 | Modbus / CAN comm-protocol templates | TBD |
| P3 | HIL SignalAnalyzer measurement library | TBD |
| P4 | Hardware fault matrix automation | partial -- 4-A backend abstraction in place |
| P5 | CI / Xray orchestration | partial -- Allure adapter + check.bat |

## 15. Skill / slash-command catalog

Reference docs landed in ``docs/skills/`` (markdown specs for the
subagent or slash-command implementations operators run from
Claude Code):

| File | Trigger | Purpose |
|------|---------|---------|
| ``docs/skills/build-schematic.md`` | ``/build-schematic <topology>`` | Generate SchematicAPI Python that builds a ``.tse`` |
| ``docs/skills/tse-to-pytest.md`` | ``/tse-to-pytest <path>`` | Parse a ``.tse`` and emit a complete pytest project |
| ``docs/skills/fault-injector.md`` | ``fault-injector`` subagent | Inject OCP/OVP/UVP/source-loss/sensor faults via the dual-path DUTInterface |
| ``docs/api-patterns.md`` | reference | ``typhoon.test.*`` + ``typhoon.api.hil`` patterns |

## 16. DUTInterface fault injection

``BaseBackend`` (``src/tools/dut/base.py``) now exposes the
Mirim Syscon DUTInterface fault-injection API as concrete defaults
that work uniformly across HIL / XCP / Hybrid / Mock:

  - ``inject_overvoltage(level_v, ramp_time_s, target)``
  - ``inject_undervoltage(level_v, ramp_time_s, target)``
  - ``inject_overcurrent(target_a, ramp_time_s, load_signal)``
  - ``inject_source_loss(target)``
  - ``inject_sensor_fault(signal, mode, value)``  -- via XCP write
  - ``expect_trip(fault_flag_signal, within_ms, poll_ms)`` -> bool
  - ``is_tripped(fault_flag_signal)`` -> bool
  - ``clear_fault(fault_flag_signal, clear_command)``

Tests in ``tests/test_dut_fault_injection.py`` (15) confirm all four
backends inherit the same surface.

## 17. Downstream pytest project template

``templates/downstream_pytest/conftest.py`` is a self-contained
DUTInterface implementation (HILSimDUT + XCPDUT) for projects that
THAA generates or operators write by hand. Copy to project root,
add ``models/<topology>.tse``, write tests against the abstract
``dut`` fixture, then run with ``DUT_MODE=vhil`` or ``DUT_MODE=xcp``.
