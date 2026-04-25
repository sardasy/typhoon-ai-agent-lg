# Multi-Agent Orchestration (Phase 4-B)

THAA can run scenarios through a multi-agent graph where each scenario
is dispatched to a domain-specialized "agent": **BMS**, **PCS**,
**Grid**, or **General**. Each agent has its own marker node in the
graph topology, its own analyzer-prompt overlay, and its own slice of
the per-domain summary in the report.

## Topology

```
load_model -> plan_tests -> classify_domains
                                |
            +---+----+----+----+
            |   |    |    |
          bms  pcs  grid general    <-- per-domain marker nodes
            |   |    |    |
            +---+----+----+
                  |
            execute_scenario
                  |
        [route_after_exec_orch]
        fail / next
                  |
        analyze_failure ---> apply_fix ---> execute_scenario  (heal loop)
        advance_scenario
                  |
        [route_after_advance]
        bms | pcs | grid | general | aggregate
                                       |
                                  generate_report -> END
```

After every `advance_scenario`, the router inspects the next scenario's
`domain` field and dispatches to the matching agent marker. When all
scenarios are done it falls through to `aggregate`, which emits a
per-agent pass / fail / error breakdown.

## Domain classification

`src/domain_classifier.py` is a pure heuristic. Inputs voted on, in
order of precedence:

1. Explicit `scenario["domain"]` (lets the YAML override the heuristic).
2. `standard_ref` prefix:
   - `IEC 62619` / `UN 38.3` / `UL 1973` -> **bms**
   - `IEEE 1547` / `IEEE 2800` / `UL 1741` -> **grid**
   - `IEC 61851` / `UL 9540` -> **pcs**
3. `parameters.fault_template` against domain-specific template sets:
   - `voltage_sag`, `voltage_swell`, `frequency_deviation`,
     `vsm_*`, `phase_jump` -> **grid**
   - `overvoltage` / `undervoltage` / `short_circuit` /
     `open_circuit` on a `V_cell_*` signal -> **bms**
4. Signal-name vote:
   - `V_cell_*`, `T_cell_*`, `BMS_*`, `SOC_*`, `Pack_*` -> **bms**
   - `Vgrid`, `Vsa`/`Vsb`/`Vsc`, `Pe`, `Qe`, `Pref`, `ROCOF` -> **grid**
   - `Vdc`, `Idc`, `Vout`, `IGBT_`, `PWM_`, `Duty` -> **pcs**
5. `category` keyword fallback (`battery`, `grid`, `inverter`).
6. Otherwise **general**.

Classification is automatic -- `plan_tests` calls
`annotate(scenarios)` after loading and `sort_by_domain(scenarios)`
so the run order matches the dispatch order (bms < pcs < grid <
general). Scenario `priority` is renumbered after the sort so the
report renders in execution order.

## Per-agent analyzer prompt overlay

`src/domain_classifier.py::DOMAIN_ANALYZER_OVERLAYS` carries one block
per domain. `analyze_failure` reads the failed scenario's `domain`
(or, as a fallback, `state.current_domain`) and appends the matching
overlay to the system prompt. Effects:

- BMS-domain failures get a prompt block listing typical BMS root
  causes (cell-balancing, OVP/UVP drift, scan-interval mistuning) and
  the writable BMS calibration parameters.
- Grid-domain failures get IEEE 1547 / 2800 context and
  `J / D / Kv` as primary calibration targets.
- PCS-domain failures get PI-tuning context and `Ctrl_Kp/Ki/Kd`
  targets.
- General gets the base prompt unchanged.

The LLM trace is also tagged with `agent:<domain>` so multi-agent runs
are filterable in LangSmith, and `run_name` becomes
`analyze_failure.<domain>.llm`.

## Running the orchestrator

### CLI

```bash
# Single-agent (default, unchanged)
python main.py --goal "..." --config configs/scenarios_vsm_gfm.yaml

# Multi-agent
python main.py --goal "..." --config configs/scenarios_vsm_gfm.yaml \
  --orchestrator
```

The orchestrator supports `--hitl` and `--checkpoint-db` since
Phase 4-D (`compile_orchestrator_graph` / `acompile_orchestrator_graph`
mirror the single-agent pair). HITL pauses before `apply_fix` exactly
as in the single-agent graph; SQLite-backed resume works the same way.

```bash
# Multi-agent + HITL + persistent SQLite resume
python main.py --goal "..." --config configs/scenarios_vsm_gfm.yaml \
  --orchestrator --hitl --checkpoint-db runs/orch.sqlite

# Resume the paused thread from a fresh shell
python main.py --resume thaa-cli-1714000000 --checkpoint-db runs/orch.sqlite \
  --orchestrator
```

### Programmatic

```python
from src.graph_orchestrator import compile_orchestrator_graph
from main import make_initial_state

app = compile_orchestrator_graph()
final = await app.ainvoke(make_initial_state("...", "configs/x.yaml"))
```

State is the same `AgentState` -- the orchestrator just adds two
fields:

- `current_domain` (str): which agent is currently active. Updated by
  agent-marker nodes and by `advance_scenario`.
- `domain_counts` (dict[str, int]): per-domain scenario count, set by
  `plan_tests`.

## Why marker nodes instead of compiled subgraphs?

The earliest design compiled a `build_domain_agent(domain)` StateGraph
per domain and used each as a node in the parent. LangGraph composes
parent + subgraph state via shared channels, so any
`Annotated[list, operator.add]` field (in our case `events`,
`results`) is *added twice*: once as the subgraph's internal
accumulator publishes, once when the subgraph's final state collapses
into the parent. The result was duplicate scenario results.

Marker nodes keep "BMS Agent" / "PCS Agent" / etc. on the topology
(visible in LangSmith spans, in mermaid diagrams, in graph
introspection) without crossing the parent-subgraph state boundary.
The shared `execute_scenario` / `analyze_failure` / `apply_fix` /
`advance_scenario` nodes handle the actual work; per-agent identity
lives in `state.current_domain` and in the analyzer prompt overlay.

If a future milestone needs per-agent state isolation (separate
checkpoints, separate signal subsets, separate retry counts), the
right abstraction is then `Send` with per-agent state schemas, not
nested `add_node(name, compiled_graph)`.

## Testing

```bash
python -m pytest tests/test_orchestrator.py -v
# 33 tests: classifier (12), annotate / sort (3), overlay (3),
# routing (8), graph structure (2), classify/aggregate (2),
# end-to-end smoke (1), domain order (2)
```

The end-to-end test runs `compile_orchestrator_graph().ainvoke(...)`
on a synthetic 3-scenario YAML (2 BMS + 1 Grid) using the mock DUT
backend. It asserts the run order is `[bms, bms, grid]`, all
scenarios pass, and the aggregator emits the per-domain summary.

## Parallel domain agents (Phase 4-F)

`compile_parallel_orchestrator_graph()` runs every non-empty domain
agent **concurrently** via LangGraph's `Send` API. Activated with
`--orchestrator --parallel`.

```
load_model -> plan_tests -> classify_domains
                                |
                          [fan_out_parallel]
                                |
   +------------+--------------+--------------+
   |            |              |              |
   bms_worker   pcs_worker     grid_worker    general_worker   <-- parallel
   |            |              |              |
   +------------+--------------+--------------+
                                |
                          [implicit join]
                                |
                            aggregate
                                |
                          generate_report
```

Each worker is an async function (`src/parallel_agents.py`) that
receives a Send branch state with **only its domain's scenarios** and
its own `scenario_index`, `current_scenario`, `diagnosis`, etc. The
worker runs the full heal loop (execute -> analyze -> simulate (if
twin) -> apply_fix -> retry -> advance) inline in Python. Results
and events merge into the parent state via `operator.add` reducers
when the worker returns.

### Why workers are not compiled subgraphs

LangGraph composes parent + subgraph state via shared channels,
which double-counts `Annotated[list, operator.add]` fields when the
subgraph collapses (we hit this in Phase 4-B). Plain async workers
return a single state delta to the parent reducer, sidestepping the
issue.

### Hardware contention

A single Typhoon HIL device cannot service two parallel callers.
`src/tools/dut/base.py::HARDWARE_LOCK` (a module-level
`asyncio.Lock`) wraps every HIL/XCP I/O method on the real-hardware
backends (`HILBackend`, `XCPBackend`, `HybridBackend`). When two
workers race on the device, the lock serializes them; meanwhile
their **Claude analyzer calls overlap** -- the headline win of
parallel mode.

`MockBackend` intentionally does NOT take the lock: tests need to
assert call ordering and adding the lock would deadlock single-task
test fixtures.

### Phase 4-J: HITL inside parallel mode

`--parallel --hitl` now works. The graph splits the heal loop in two:

1. **Parallel diagnostic phase** -- workers execute scenarios, run
   `analyze_failure` (Claude) on failures, and **defer** the apply.
   Each worker appends `(scenario, diagnosis)` to a shared
   `pending_fixes` list (operator.add reducer merges across siblings).
2. **Serial replay loop** -- after fan-out join, the parent walks the
   queue one entry at a time:

   ```
   next_pending_fix -> [interrupt before approve_fix]
                    -> approve_fix -> (simulate_fix?) -> apply_fix -> execute_scenario
                    -> route_has_pending (loop)
   ```

   The interrupt fires fresh for each pending fix, so the operator
   reviews and approves one calibration at a time. Audit trail
   (`src/audit.py`) records every decision.

The win: Claude diagnostic calls overlap (the expensive part) while
ECU writes stay strictly sequential and approved.

```bash
# Phase 4 full stack with parallel diagnosis + per-fix HITL
python main.py --goal "..." --config configs/scenarios_vsm_gfm.yaml \
  --orchestrator --parallel --twin \
  --hitl --checkpoint-db runs/parallel_hitl.sqlite
```

### Limitations

- **Same single HIL device per `device_id`.** Real speedup is bounded
  by per-device locks -- the win is in concurrent Claude API calls.
  Use Phase 4-I `device_id` routing for true I/O parallelism across
  multiple HILs.

## Per-agent RAG namespaces (Phase 4-G)

`load_model` now also populates `state["rag_context_by_domain"]`
with one bucket per domain (queries the index with `domain=X`).
`analyze_failure` picks the right bucket per failed scenario:

```python
by_domain = state.get("rag_context_by_domain") or {}
rag_ctx = by_domain.get(scenario["domain"]) or state["rag_context"]
```

Falls back to the global pull when a namespace is empty (e.g. running
on a fresh, sparsely-tagged index). See `docs/RAG_INDEXING.md` for
how documents are tagged.

## Out of scope (future)

- Multi-device HIL (one parent agent per physical device).
