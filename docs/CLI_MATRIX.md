# CLI Compatibility Matrix

THAA's `main.py` exposes ~17 flags accumulated across Phases 1-4.
Many are independent; a few interact in ways that are easy to miss.
This page is the source-of-truth for what combinations are supported.

## Flag inventory

| Flag | Phase | Purpose |
|------|-------|---------|
| `--goal "..."` | 1 | Natural-language test objective (Claude Planner input) |
| `--config <yaml>` | 1 | Hardware + scenario YAML (default `configs/model.yaml`) |
| `--server` | 1 | FastAPI dashboard mode (no `--goal` required) |
| `--host` / `--port` | 1 | Server bind address |
| `--hitl` | 3 | Pause before `apply_fix` for operator approval |
| `--checkpoint-db <path>` | 3 | SQLite-backed persistent state (resume across restarts) |
| `--resume-thread <id>` | 3 | Resume a paused thread (auto-enables HITL) |
| `--list-threads` | 3 | Print thread IDs in the checkpoint DB and exit |
| `--dut-backend {hil,xcp,hybrid,mock}` | 4-A | Which DUT abstraction backend |
| `--a2l-path <path>` | 4-A | A2L file (xcp/hybrid backends) |
| `--xcp-uri <uri>` | 4-A | XCP transport URI |
| `--orchestrator` | 4-B | Multi-agent graph (BMS/PCS/Grid markers) |
| `--twin` | 4-C | Digital-twin gate before `apply_fix` |
| `--parallel` | 4-F | Send-based fan-out (requires `--orchestrator`) |
| `--preflight` | 4-H | Run env / config / HIL / XCP / RAG / twin checks and exit |
| `--preflight-strict` | 4-H | With `--preflight`: WARN-level exits 2 |

## Compatibility matrix

|                | `--orchestrator` | `--parallel` | `--twin` | `--hitl` | `--checkpoint-db` |
|----------------|:----------------:|:------------:|:--------:|:--------:|:-----------------:|
| `--orchestrator` | -- | ✅ | ✅ | ✅ | ✅ |
| `--parallel`     | **required** | -- | ✅ | ✅ *(4-J)* | ✅ *(4-J)* |
| `--twin`         | ✅ | ✅ | -- | ✅ | ✅ |
| `--hitl`         | ✅ | ✅ *(4-J)* | ✅ | -- | ✅ |
| `--checkpoint-db`| ✅ | ✅ *(4-J)* | ✅ | ✅ | -- |

**Legend:** ✅ supported · ⚠️ ignored with warning · ❌ not allowed

### Notable interactions

- **`--parallel` requires `--orchestrator`.** Used alone, the flag has
  no effect on the single-agent graph.
- **`--parallel` + `--hitl`** (Phase 4-J) defers heals: workers
  diagnose in parallel, then the parent applies each fix serially
  with operator approval. Pause is BEFORE `approve_fix` (one
  interrupt per pending fix).
- **`--resume-thread` implies `--hitl`.** Resuming a thread only makes
  sense for paused HITL runs. The runner auto-enables HITL when the
  flag is supplied.
- **`--server` ignores graph-mode flags.** The web dashboard runs
  `compile_graph()` in single-agent mode. Use the CLI for orchestrator
  / parallel modes.
- **`--preflight` short-circuits.** Runs the checks and exits before
  building the graph; combination with any other flag is harmless.

## Recommended ramp on real hardware

A safe progression that touches one Phase 4 feature at a time:

1. `--dut-backend hil` (single-agent, default backend) -- baseline.
2. `--dut-backend hybrid --a2l-path firmware.a2l` -- adds real ECU.
3. `+ --orchestrator` -- enables BMS/PCS/Grid markers and per-agent
   prompts.
4. `+ --twin` -- adds simulate_fix vetoes (no-op / out-of-range /
   wrong-direction).
5. `+ --hitl --checkpoint-db runs/op.sqlite` -- adds operator approval
   with crash-recovery.
6. **Skip** `+ --parallel` for HITL runs. Switch to it only for
   batch regressions where you don't need approval.

## Examples

```bash
# Phase 4 full stack (no parallel, with operator approval)
python main.py --goal "ESS regression" \
  --config configs/scenarios_250123.yaml \
  --dut-backend hybrid --a2l-path firmware.a2l \
  --orchestrator --twin \
  --hitl --checkpoint-db runs/regression.sqlite

# Batch regression (parallel, no approval)
python main.py --goal "Multi-rig sweep" \
  --config configs/scenarios_multi_device.yaml \
  --dut-backend mock \
  --orchestrator --parallel --twin

# Pre-flight only (CI gate)
python main.py --preflight --preflight-strict --a2l-path firmware.a2l
```

## See also

- `docs/REAL_TYPHOON_BRINGUP.md` -- 7-step bring-up checklist
- `docs/HITL.md` -- HITL UX and key bindings
- `docs/MULTI_AGENT.md` -- orchestrator + parallel internals
