# Persistent HITL State with SQLite

THAA's HITL mode can now persist its paused-graph state to a SQLite
file. A run that paused on `apply_fix` survives shell exits, crashes,
and host reboots; an operator can resume the exact same thread from a
fresh process.

## Why

When the graph pauses for operator approval, the diagnosis + retry
counter + scenario state live in the checkpointer. Previously that was
`MemorySaver` (in-process only), so:

- Closing the terminal lost the pending approval.
- A crash during review wasted the Claude analyses already performed.
- Long-lived safety reviews (hours to days, per CLAUDE.md invariant #2)
  weren't practical.

With `AsyncSqliteSaver` the checkpoint lives on disk. Every paused run
is addressable by a `thread_id`; any process with the DB path can list,
resume, or delete it.

## Enabling SQLite persistence

### CLI

```bash
# Start a HITL run with SQLite persistence
python main.py --goal "VSM heal demo" \
  --config configs/scenarios_heal_demo.yaml \
  --hitl --checkpoint-db runs/hitl.sqlite
# ... stop any time with Ctrl+C (no approval needed)

# From a fresh shell, list persisted threads
python main.py --list-threads --checkpoint-db runs/hitl.sqlite
# [thaa] 1 thread(s) in runs/hitl.sqlite:
#   THREAD_ID            CHECKPOINTS   LAST_CHECKPOINT_ID
#   thaa-cli-1713600000            6   1f13cd97-...

# Resume the same thread (auto-enables HITL, skips goal)
python main.py --resume-thread thaa-cli-1713600000 \
  --checkpoint-db runs/hitl.sqlite
# The HITL prompt reappears with the original diagnosis.
# Answer 'y' (or 'n') -- the graph continues from the checkpoint.
```

### Environment variable

Equivalent to `--checkpoint-db`:

```bash
export THAA_CHECKPOINT_DB=runs/hitl.sqlite
python main.py --goal "..." --hitl
```

### Programmatic (async)

```python
from src.graph import acompile_graph

app = await acompile_graph(
    hitl=True,
    checkpoint_db="runs/hitl.sqlite",
)
cfg = {"configurable": {"thread_id": "my-thread"}}

try:
    async for step in app.astream(initial_state, config=cfg):
        ...                                    # graph pauses before apply_fix
    snap = await app.aget_state(cfg)
    if "apply_fix" in snap.next:
        # ...review diagnosis, resume with input=None to approve
        async for step in app.astream(None, config=cfg):
            ...
finally:
    await app.checkpointer.conn.close()        # drop file lock
```

## Two compile entry points

| Function | Use when |
|----------|----------|
| `compile_graph(...)` (sync) | No SQLite needed (MemorySaver fallback), unit tests, non-HITL runs |
| `acompile_graph(...)` (async) | SQLite-backed HITL, required for `checkpoint_db` |

Both accept the same `hitl` / `interrupt_nodes` / `checkpoint_db`
parameters. `acompile_graph` is async because `aiosqlite.connect` is
await-only. Call it from inside `asyncio.run` (or an already-running
loop).

## How the persistence works

1. `acompile_graph(checkpoint_db=...)` calls `_open_async_sqlite_saver()`.
2. That helper:
   - Creates the DB file if missing.
   - Opens a transient `sqlite3.Connection` to run `SqliteSaver.setup()`
     which creates the `checkpoints` and `writes` tables. Idempotent.
   - Opens an `aiosqlite.Connection` inside the caller's event loop and
     wraps it in `AsyncSqliteSaver`.
3. LangGraph calls the saver's `aput_writes` / `aput` after each node,
   persisting state transitions.
4. On `interrupt_before=["apply_fix"]`, the graph yields control back to
   the CLI **with the checkpoint already flushed** -- nothing else needs
   to happen for durability.
5. On resume, `acompile_graph(...)` opens a new connection to the same
   DB; `aget_state(cfg)` reads the latest checkpoint and `astream(None,
   config=cfg)` picks up exactly where it paused.

## Schema

The DB has three tables (created by LangGraph's `SqliteSaver.setup`):

- `checkpoints` — one row per state transition, indexed by `(thread_id,
  checkpoint_id)`
- `writes` — per-channel updates (events, scenarios, etc.)
- `migrations` — LangGraph schema version tracking

Inspect directly:

```bash
sqlite3 runs/hitl.sqlite "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id;"
```

## Cleanup

To discard an abandoned thread without dropping the DB:

```python
import sqlite3
conn = sqlite3.connect("runs/hitl.sqlite")
conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", ("abandoned-id",))
conn.execute("DELETE FROM writes WHERE thread_id = ?", ("abandoned-id",))
conn.commit()
conn.close()
```

(A `thaa-cli cleanup` command is a natural follow-up.)

## Deterministic demo

`scripts/demo_sqlite_resume.py` exercises the full lifecycle with stubbed
graph nodes (no ANTHROPIC_API_KEY, no HIL). Output:

```
=== PHASE 1: start HITL run, pause before apply_fix ===
  next nodes:   ['apply_fix']
  diagnosis:    J=0.3 (conf=90%)
  db size:      20480 bytes
  connection CLOSED (simulated process exit)

=== PHASE 2: --list-threads from a fresh process ===
[thaa] 1 thread(s) in runs/hitl.sqlite:
  THREAD_ID     CHECKPOINTS   LAST_CHECKPOINT_ID
  demo-thread             6   1f13cd97-...

=== PHASE 3: resume thread 'demo-thread' and approve apply_fix ===
  recovered diagnosis:  J=0.3
  after resume next:    ['apply_fix']   # interrupts again on next loop iteration
```

The "recovered diagnosis: J=0.3" line is the proof point: the same
diagnosis value round-trips through three graph instances.

## Trade-offs vs. other checkpointers

| Saver | Persistence | Multi-process | Notes |
|-------|-------------|---------------|-------|
| `MemorySaver` (default w/ HITL) | None | No | Fastest; fine for short approvals within one shell |
| `AsyncSqliteSaver` (this) | File | Yes (one writer at a time) | Good for single-host operation teams |
| `AsyncPostgresSaver` | Network | Yes (concurrent) | Future option for multi-operator deployments |

CLAUDE.md Phase 4 roadmap lists Postgres as a next step; swapping is a
one-line change to `_open_async_sqlite_saver`.

## Tests

`tests/test_sqlite_checkpointer.py` (7 tests) covers:

- Sync factory: DB creation + schema setup
- `acompile_graph` attaches an AsyncSqliteSaver with live `conn`
- `THAA_CHECKPOINT_DB` env var is honoured
- Sync `compile_graph()` without `checkpoint_db` still works
  (backwards-compatible MemorySaver path)
- **State persistence across graph instances** (rebuild graph with
  same db + thread_id, state intact)
- `_list_threads` CLI utility prints persisted runs
- `_list_threads` with missing DB file returns non-zero exit

Total suite after this change: **182 tests**.
