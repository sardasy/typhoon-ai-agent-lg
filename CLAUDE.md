# CLAUDE.md — Typhoon HIL AI Agent (LangGraph Edition)

## Project identity

This is **THAA** (Typhoon HIL AI Agent) — a LangGraph-based AI agent system that
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
- All 160 tests pass without any external dependencies (no API key, no HIL, no XCP)

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
# Run tests (160 tests, should all pass — run this after every change)
cd typhoon_ai_agent_lg && python -m pytest tests/ -v

# Run a single test goal (requires ANTHROPIC_API_KEY)
python main.py --goal "BMS overvoltage protection, 4.2V, 100ms"

# Launch web dashboard
python main.py --server --port 8000

# Type check
python -m mypy src/ --ignore-missing-imports
```

### Claude Code workflow

When working on this project with Claude Code, follow this sequence:

1. **Read before writing.** Always read the relevant node file and `state.py`
   before modifying any node. Understand what state fields the node reads/writes.
2. **Run tests after every change.** `python -m pytest tests/ -v` must stay at
   160 passed (or more if you added tests). Never commit with failing tests.
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
| `graph.py` | StateGraph topology, conditional edge functions, MAX_HEAL_RETRIES | state, all nodes |
| `nodes/load_model.py` | HIL model loading, signal discovery, RAG context fetch | tools/hil, tools/rag |
| `nodes/plan_tests.py` | Claude Planner call, JSON plan parsing | langchain-anthropic |
| `nodes/execute_scenario.py` | Stimulus application, waveform capture, pass/fail evaluation | tools/hil |
| `nodes/analyze_failure.py` | Claude Analyzer call, diagnosis JSON parsing | langchain-anthropic |
| `nodes/apply_fix.py` | XCP calibration write, safety validation | tools/xcp, validator |
| `nodes/advance_scenario.py` | scenario_index increment, state cleanup | nothing |
| `nodes/generate_report.py` | Simulation stop, Jinja2 HTML rendering | tools/hil, reporter |
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
- **Phase 4 (future):** Multi-agent subgraphs (BMS Agent, PCS Agent, Grid Agent
  as separate StateGraphs coordinated by an orchestrator), human-in-the-loop
  breakpoints for safety-critical decisions, digital twin integration,
  DUT abstraction (same test code for HIL model and real ECU via pyXCP).
