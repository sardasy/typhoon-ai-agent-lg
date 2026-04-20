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
