# Real Typhoon HIL Bring-up Notes

Status of running THAA against a real Typhoon HIL Control Center installation
(currently tested against THCC 2026.1 SP1 + HIL404).

## Environment

| Item | Value |
|------|-------|
| THCC install | `C:\abc\Typhoon HIL Control Center 2026.1 sp1\` |
| Bundled Python | `python3_portable\python.exe` (Python 3.11.4) |
| Typhoon API | `typhoon.api.hil`, `typhoon.test.capture`, `typhoon.api.schematic_editor` |
| Test device | HIL404, serial `00404-05-00190`, hw rev 1.3, build 2026-2-4 |
| Device resources | 16 AI / 16 AO (-10..10V), 32 DI / 32 DO, 2x CAN, 64M-sample scope |

## Launcher

```bash
# All commands routed through Typhoon's bundled Python
scripts\run_with_typhoon.bat pytest tests/
scripts\run_with_typhoon.bat python main.py --goal "..." --config configs/scenarios_vsm_gfm.yaml
```

## Verified working

- ✅ `typhoon.api.hil` import + signal discovery (`get_analog_signals()`, `get_scada_inputs()`)
- ✅ Model load: `hil.load_model(file=cpd, vhil_device=False)` targets the
  connected HIL404 by default; pass `vhil_device=True` to force the VHIL
  simulator instead.
- ✅ Simulation start / stop lifecycle
- ✅ SCADA input writes via `set_scada_input_value("P_ref"|"J"|"D"|"Kv", value=...)` —
  `_signal_write` now auto-tries SCADA first, falls back to source.
- ✅ 3-phase source: `set_source_sine_waveform("Vgrid", rms=..., frequency=..., phase=...)`
  drives all three phases automatically.
- ✅ Single-shot reads: `read_analog_signal(name=...)` for Va/Pe/Qe/w/VDC etc.
- ✅ Compile via `SchematicAPI.load(tse) + .compile()` — produces `.cpd` next to `.tse`.

## Known issues

### 1. `start_capture` API parameter rename
Old name `trigger_type` removed; new API uses `trigger_source` /
`trigger_threshold` / `trigger_edge`. Already patched in
`src/tools/hil_tools.py::_capture()`.

### 2. Triggered captures sometimes return empty buffer
When the trigger condition never fires (e.g., voltage never crosses the
threshold within the window), `get_capture_results()` raises
`Exception: There is no data in capture buffer.` THAA currently swallows
this as a non-fatal `{"error": ...}` and the evaluator falls back to PASS
when no waveform stats exist.

**Action item**: tighten `_evaluate()` so that "no waveform" is treated as
ERROR, not PASS. Or use untriggered (duration-only) captures for
steady-state scenarios.

### 3. Mock-only tests skipped
`TestHILToolsMock` is marked `@skipif(HAS_TYPHOON)` because its assertions
are written for the mock-mode return shape. Tests still run on Mock-only
machines.

### 4. Signal-name nuances
| Concept | TSE schematic name | Typhoon API name |
|---------|-------------------|------------------|
| Power references | Component label `P_ref` / `Q_ref` | SCADA input `P_ref` / `Q_ref` |
| VSM tunables | `J` / `D` / `Kv` | Same (SCADA inputs) |
| Grid source | "Three Phase Voltage Source" named `Vgrid` | Single source name `Vgrid` |
| Phase voltage probes | `Va` / `Vb` / `Vc` | Analog signals `Va` / `Vb` / `Vc` |
| Grid phase probes | n/a | `Vgrid_a` / `Vgrid_b` / `Vgrid_c` (read-only) |

The `scenarios_vsm_gfm.yaml` already uses these correct names.

## Bring-up sanity script

```python
import typhoon.api.hil as hil

cpd = r"C:\Users\junpr\Downloads\invertertest Target files\invertertest.cpd"
hil.load_model(file=cpd, offlineMode=False, vhil_device=True)
hil.start_simulation()

hil.set_source_sine_waveform("Vgrid", rms=230/1.732, frequency=50.0, phase=0.0)
hil.set_scada_input_value("P_ref", value=5000.0)
hil.set_scada_input_value("J", value=0.3)
hil.set_scada_input_value("D", value=10.0)

import time; time.sleep(3.0)
print("Pe =", hil.read_analog_signal(name="Pe"), "W")
print("w  =", hil.read_analog_signal(name="w"), "rad/s")

hil.stop_simulation()
```

Expected output: non-zero `Pe` and `w ≈ 314 rad/s` (50 Hz) within a few
seconds.

## Next steps

1. **Capture robustness** — switch to untriggered captures for steady-state
   scenarios; only use trigger when it's intrinsic to the test (e.g.
   voltage sag detection).
2. **Evaluator strictness** — change `_evaluate()` default from PASS to
   ERROR when no waveform stats arrive.
3. **Real device test** — HIL404 is wired and the code default is now
   `vhil_device=False`. Run one scenario end-to-end against hardware and
   confirm analog/digital I/O match expected values before trusting the
   healing loop.
4. **Heal loop demo** — pick one GFM scenario, intentionally mistune `J`
   to make it fail, then watch the analyze_failure → apply_fix loop
   converge.

## Capture tuning (post-bring-up)

### Streaming capture status (typhoon.test.capture)
- `start_capture()` accepts the new `trigger_source / trigger_threshold /
  trigger_edge` parameter set (old `trigger_type` removed).
- `get_capture_results(wait_capture=True)` **blocks indefinitely on VHIL**
  (verified). With `wait_capture=False`, the buffer is reported empty even
  after the API logs "desired number of samples are already captured".
- This appears to be a VHIL limitation; on real HIL hardware it may work.

### Polled capture (the fallback)
- Implemented in `HILToolExecutor._capture_polled()`.
- Caps effective rate at 50 Hz; uses single `read_analog_signal()` calls.
- Empirical VHIL latency: **~250 ms / read** — limits us to ~2 samples per
  second per signal. Sufficient for steady-state mean / RMS measurements,
  not for fast transients.
- Activated automatically when streaming returns empty buffer, or
  explicitly via `force_polling: True` in scenario parameters.

### Recommendation
- Steady-state IEEE 2800 GFM scenarios (THD, voltage source behavior,
  active power tracking) are validatable on VHIL with polled capture.
- Dynamic scenarios (FFCI ≤20 ms response, phase jump resync, virtual
  inertia step response) need either:
  - Real Typhoon HIL hardware (where streaming capture should work), OR
  - The standalone pytest project under `test_project_vsm_gfm/` driven
    from inside `typhoon.test`'s native runner (different capture engine).

## Bring-up checklist (Phase 4-H)

Run these in order. Stop at the first failure -- later checks assume
earlier ones pass.

| # | Step | Command | Pass criterion |
|---|------|---------|----------------|
| 1 | Bundled Python visible | `scripts\run_with_typhoon.bat python -V` | Prints Python 3.11.4 |
| 2 | THAA imports cleanly | `scripts\run_with_typhoon.bat python -c "import src.graph"` | Exit 0 |
| 3 | Pre-flight (env + deps + RAG + twin) | `scripts\run_with_typhoon.bat python scripts\preflight.py` | 0 FAIL |
| 4 | HIL device responds | `scripts\run_with_typhoon.bat python scripts\preflight.py --hil` | `hil.signals` PASS with > 30 signals |
| 5 | Optional: A2L parses | `scripts\run_with_typhoon.bat python scripts\preflight.py --xcp --a2l-path firmware.a2l` | `xcp.a2l` PASS |
| 6 | Single-scenario smoke | `scripts\run_with_typhoon.bat python main.py --goal "..." --config configs\scenarios.yaml` | Final report shows ≥1 PASS |
| 7 | Multi-agent + twin smoke | `scripts\run_smoke_real.bat` (or with A2L: `scripts\run_smoke_real.bat firmware.a2l`) | Exit 0 |

`--preflight` is also wired into `main.py` directly:

```bash
scripts\run_with_typhoon.bat python main.py --preflight --a2l-path firmware.a2l
scripts\run_with_typhoon.bat python main.py --preflight --preflight-strict
```

The strict variant returns 2 on any WARN -- useful in CI before
authorising a real-hardware run. Default (non-strict) returns 0 even
when optional components are missing (e.g. no pyxcp installed).

## Phase 4 stack on real hardware

Each Phase 4 feature is independently opt-in. Recommended ramp on
HIL404 + ECU:

1. **4-A only** (`--dut-backend hil`): plain HIL run, no XCP.
   Validates the existing single-graph flow on the device.
2. **4-A hybrid** (`--dut-backend hybrid --a2l-path ...`): HIL plant
   + ECU calibration via XCP. Validates the heal loop.
3. **4-B serial multi-agent** (`--orchestrator`): adds domain
   classification + per-agent prompts.
4. **4-C twin** (`+ --twin`): adds simulate_fix vetoes (no-op /
   out-of-range / wrong-direction).
5. **4-D HITL persistence** (`+ --hitl --checkpoint-db ...`):
   operator approval + resume across restarts.
6. **4-E XCP DAQ** (already covered by `xcp` / `hybrid` backends; no
   extra flag).
7. **4-F parallel** (`--orchestrator --parallel`): concurrent
   analyzer calls, hardware serialized by `HARDWARE_LOCK`. **Not
   compatible with HITL / SQLite yet** -- the runner warns and runs
   without them.
8. **4-G domain RAG** (automatic via `load_model` once the index has
   been built with `python scripts/index_knowledge.py`).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `hil.load_model returned False` | Wrong `.cpd` path or device not found | Re-compile via SchematicAPI; verify `hil.set_simulation_step` runs |
| `start_capture` raises on `trigger_type` | Old THCC version | Upgrade to 2026.1 SP1+ or pin `trigger_source/threshold/edge` |
| `get_capture_results()` empty buffer | Trigger never fired | Use `force_polling: true` in scenario, or remove the trigger |
| `xcp.a2l` PASS but XCP write rejected | Param not in whitelist | Add it to `XCPToolExecutor.WRITABLE_PARAMS` after safety review |
| Heal loop never converges, twin says "uncertain" | Param has no `PLAUSIBLE_RANGES` entry | Extend `src/twin.py::PLAUSIBLE_RANGES` for that param |
| Parallel run mixes events from different domains | Expected -- workers run concurrently | Filter on `event.data.domain` or `event.node` for per-agent view |
| `--orchestrator --parallel --hitl` warns and skips HITL | Known limitation (Phase 4-F) | Drop `--parallel` for HITL runs; future milestone will lift this |

## Multi-device HIL (Phase 4-I)

The agent supports multiple physical HIL/ECU devices in one run.
Scenarios opt in via a `device_id` field in YAML; `state["device_pool"]`
maps each id to a per-device DUT config overlay.

```yaml
# configs/scenarios.yaml
scenarios:
  bms_pack_a:
    description: BMS overvoltage on pack A
    device_id: hil_404_a       # Phase 4-I: routes to rig A
    parameters: {target_cell: 1, fault_voltage: 4.3, ramp_duration_s: 0.2}
    measurements: [V_cell_1]
    pass_fail_rules: {relay_must_trip: true}
  bms_pack_b:
    description: BMS overvoltage on pack B
    device_id: hil_404_b       # ... and rig B
    parameters: {target_cell: 1, fault_voltage: 4.3, ramp_duration_s: 0.2}
    measurements: [V_cell_1]
    pass_fail_rules: {relay_must_trip: true}
```

```python
# main.py invocation (programmatic):
from main import make_initial_state
state = make_initial_state(
    "Two-rig regression",
    "configs/scenarios.yaml",
    dut_backend="hil",
    dut_config={"shared_setting": True},
    device_pool={
        "hil_404_a": {"a2l_path": "fw_a.a2l"},
        "hil_404_b": {"a2l_path": "fw_b.a2l"},
    },
)
```

Scenarios with no `device_id` use `"default"` (current behavior --
fully backward-compatible).

### How it serializes

`src/tools/dut/base.py::get_hardware_lock(device_id)` returns a
distinct `asyncio.Lock` per device. Backends call `self.lock()`
around every I/O call. Effects:

- Scenarios on **different** devices: their I/O overlaps. The parallel
  orchestrator can drive all rigs concurrently.
- Scenarios on the **same** device: serialized. Two parallel domain
  agents that both target `hil_404_a` queue on its lock.

The legacy module-level `HARDWARE_LOCK` is preserved as the
`"default"` device's lock so older code keeps working unchanged.

### Bring-up addition

After step 4 in the checklist, add a multi-device probe **only if**
you have more than one HIL on the same host:

```bash
# Each device must respond independently. Discover them first:
scripts\run_with_typhoon.bat python -c "import typhoon.api.hil as h; print(h.list_connected_devices())"
```

Then re-run preflight per device-id (using `--config` overlays that
point each device at its own `.cpd` and `.a2l`).
