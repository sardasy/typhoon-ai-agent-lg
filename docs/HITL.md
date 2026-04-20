# Human-in-the-Loop (HITL)

THAA can pause before any safety-critical node so an operator can review
the proposed action before it executes. By default the pause point is
**`apply_fix`** — the XCP calibration write. This implements CLAUDE.md
safety invariant #2 (XCP whitelist + human approval for safety-critical
parameters).

## Enabling HITL

### CLI

```bash
python main.py --goal "..." --config configs/scenarios_vsm_gfm.yaml --hitl
```

When the agent reaches a fix recommendation it pauses and prompts:

```
┌──────────── HITL approval needed ────────────┐
│ Scenario: vsm_inertia_heal_demo              │
│ Root cause: VSM inertia constant J=0.05 ...  │
│ Confidence: 92%                              │
│ Proposed action: xcp_calibration J = 0.3     │
└──────────────────────────────────────────────┘
Approve apply_fix? [y]es / [n]o (escalate) / [a]bort:
```

| Key | Effect |
|-----|--------|
| `y` / `yes` | Approve — graph resumes from checkpoint, calls `apply_fix` |
| `n` / `no` | Reject — graph forces `escalate` (skips fix, advances scenario) |
| `a` / `abort` / `q` | Stop the run |

### Environment variable

```bash
export THAA_HITL=1               # Linux / git-bash
$env:THAA_HITL = "1"             # Windows PowerShell
python main.py --goal "..."      # No --hitl flag needed
```

### Programmatic

```python
from src.graph import compile_graph

# Default interrupt point: apply_fix
app = compile_graph(hitl=True)

# Pause before a different node (or multiple)
app = compile_graph(hitl=True, interrupt_nodes=("execute_scenario", "apply_fix"))

# Bring your own checkpointer (e.g. SQLite)
from langgraph.checkpoint.sqlite import SqliteSaver
app = compile_graph(hitl=True, checkpointer=SqliteSaver.from_conn_string("hitl.db"))
```

## How it works

1. `compile_graph(hitl=True)` attaches a `MemorySaver` checkpointer and
   declares `interrupt_before=["apply_fix"]`.
2. When the graph reaches `apply_fix`, LangGraph saves state and stops
   `astream` cleanly (no exception).
3. The CLI loop calls `app.get_state(config)`. If `snapshot.next` is
   non-empty, the operator is prompted with the diagnosis details.
4. **Approve** → resume with `astream(None, config)` — LangGraph picks up
   from the saved checkpoint.
5. **Reject** → `app.update_state(config, {"diagnosis": {"corrective_action_type": "escalate"}})`
   then resume; `route_after_analysis` now picks the `"escalate"` branch
   and skips `apply_fix`.
6. **Abort** → drop out of the loop; the graph state remains paused (the
   thread can be resumed later by re-invoking with the same `thread_id`).

## State persistence

The default `MemorySaver` is in-process — checkpoints disappear when the
Python process exits. For multi-day reviews or crash recovery, use
**SQLite persistence**:

```bash
python main.py --goal "..." --hitl --checkpoint-db runs/hitl.sqlite
python main.py --list-threads --checkpoint-db runs/hitl.sqlite
python main.py --resume-thread <id> --checkpoint-db runs/hitl.sqlite
```

See [SQLITE_CHECKPOINTER.md](SQLITE_CHECKPOINTER.md) for the full guide.
Postgres is a drop-in replacement (`AsyncPostgresSaver`) for multi-operator
deployments; see the CLAUDE.md Phase 4 roadmap.

## Custom decision rules

Out of the box, HITL gates only `apply_fix`. To gate something else:

```python
# Pause before any test execution that includes destructive faults
app = compile_graph(
    hitl=True,
    interrupt_nodes=("execute_scenario",),
)
```

Or wrap the node with your own pre-check that returns a state-modifying
update (e.g. `{"error": "rejected"}`) instead of relying on
`update_state`.

## Web mode

The current FastAPI dashboard does not yet expose `/api/approve` and
`/api/reject` for HITL. It's straightforward to add:

```python
@app.post("/api/approve/{thread_id}")
async def approve(thread_id: str):
    cfg = {"configurable": {"thread_id": thread_id}}
    # Resume by streaming with input=None
    async def gen():
        async for step in graph_app.astream(None, config=cfg):
            yield {"event": "node", "data": json.dumps(step)}
    return EventSourceResponse(gen())
```

Tracked as a roadmap item in CLAUDE.md.

## Tests

`tests/test_hitl.py` covers:
- Default vs `--hitl` graph compilation (checkpointer attached / not)
- `THAA_HITL` env var enables / disables
- Custom `interrupt_nodes` argument
- End-to-end pause-before-apply_fix using stubbed analyzer
  (so it runs without `ANTHROPIC_API_KEY`)

```
pytest tests/test_hitl.py -v
```

All 6 tests passing alongside the existing 111 — total **117 tests**.
