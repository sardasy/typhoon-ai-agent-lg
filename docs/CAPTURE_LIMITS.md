# VHIL Capture: What Works, What Doesn't

Definitive notes from the session that tried every Typhoon HIL capture path
from external Python on **THCC 2026.1 SP1 + VHIL**. Use this to decide
which scenario rules can realistically run without physical HIL hardware.

## TL;DR

- ✅ **Steady-state DC reads** (single `read_analog_signal`) work.
- ✅ **SCADA input writes** (P_ref / J / D / Kv) work.
- ❌ **Streaming capture via `typhoon.test.capture`** hangs on
  `wait_capture=True`, returns empty buffer on `wait_capture=False`.
- ❌ **Low-level `hil.start_capture`** with `fileName=...` or
  `dataBuffer=...` returns True immediately but neither the file nor the
  buffer is ever populated.
- ⚠️ **Polled capture** (repeated `read_analog_signal`) is limited to
  **~4 Hz effective** (250 ms / call via the RPC bridge).
- 🚫 Consequently AC signals at 50/60 Hz **alias** to a single phase sample
  on VHIL — cannot measure true RMS, THD, or transients externally.

## Evidence

### 1. `typhoon.test.capture`
```python
start_capture(duration=0.1, signals=['Va', 'Ia'])
r = get_capture_results(wait_capture=False)
# -> Exception: There is no data in capture buffer.
```
Capture completes (`[CAPTURE MESSAGE] stop_capture() will be ignored since
desired number of samples are already captured!`) but results are never
retrievable from the external process.

With `wait_capture=True` the call **blocks indefinitely** — verified with
90 s timeouts.

### 2. Low-level `hil.start_capture`
```python
cpSettings = [20, 3, 2048, False]
hil.start_capture(cpSettings=..., trSettings=['Forced'],
                  chSettings=[['Va','Ia','Pe']], fileName='vhil_cap.csv')
# ok=True
# hil.capture_in_progress() -> True forever
# file never created
```

Same behaviour with `dataBuffer=[]` — buffer stays empty.

### 3. Polled capture (what we actually ship)
```python
t0 = time.time()
for i in range(8):
    ti = time.time()
    v = hil.read_analog_signal(name='Va')
    print(f'iter={i}  dt={1000*(time.time()-ti):.1f}ms  Va={v:.3f}')

# iter=0  dt=249.9ms  Va=42.202
# iter=1  dt=260.5ms  Va=42.202   <-- aliased to same phase!
# iter=2  dt=263.9ms  Va=42.202
# ...
```

The 250 ms RPC round-trip is a hard floor — calls serialise across the
Typhoon gateway. When 250 ms mod 20 ms (50 Hz period) ≈ 0 ms the samples
land on the same phase every time.

## What this means for THAA scenarios

| Rule family | External VHIL | Real Typhoon HIL* | `test_project_vsm_gfm/` pytest** |
|-------------|---------------|--------------------|----------------------------------|
| DC steady-state (V_cell, VDC, Pe mean) | ✅ | ✅ | ✅ |
| Digital relay / lock-out assertion | ✅ | ✅ | ✅ |
| Active/reactive power tracking | ⚠️ DC only | ✅ | ✅ |
| AC RMS / voltage threshold | ❌ aliased | ✅ | ✅ |
| THD / individual harmonics | ❌ | ✅ | ✅ |
| ROCOF from `w` probe | ⚠️ coarse | ✅ | ✅ |
| FFCI response <= 20 ms | ❌ | ✅ | ✅ |
| Phase jump resync <= 1 s | ❌ | ✅ | ✅ |
| Rise / settling time under step | ⚠️ if step amplitude > noise | ✅ | ✅ |

\* with `vhil_device=False` and an HIL606/HIL101 connected over Ethernet
\** pytest project runs *inside* `typhoon.test`'s native capture pipeline,
which bypasses the external-Python RPC bottleneck

## How THAA handles this today

1. `src/tools/hil_tools.py::_capture` tries `typhoon.test.capture.start_capture`
   first (in case we're on real HW where it works), then falls back to
   polled `read_analog_signal`.
2. `src/waveform_analytics.py` computes `rms`, `rise_time_ms`,
   `settling_time_ms`, `overshoot_percent`, `thd_percent`,
   `rocof_hz_per_s` **only when the captured sample count supports it**;
   otherwise those fields stay `None`.
3. `src/evaluator.py` returns `ERROR` (not `PASS`) when a rule needs a
   metric that's `None` — e.g. `voltage_thd_max_pct` explicitly reports:

   ```
   error: voltage_thd_max_pct: THD not computed (scenario needs analysis: [thd])
   ```

   Combined with the 2026-04-20 evaluator rewrite, this means the agent
   no longer reports fake PASSes for dynamic scenarios on VHIL.

## Recommended mapping

| Environment | How to run scenarios | Expected pass rate |
|-------------|---------------------|---------------------|
| Pure mock (no HIL) | `python main.py --config configs/scenarios_heal_demo.yaml` | Intentional failure demo; heal loop converges |
| VHIL (external Python) | Only steady-state YAMLs, e.g. `scenarios.yaml::ovp_boundary` | Digital / DC rules pass; AC rules correctly ERROR |
| VHIL via TyphoonTest IDE | `pytest test_project_vsm_gfm/` from the bundled IDE | All 28 IEEE 2800 GFM tests runnable |
| Real HIL606/101 | `python main.py --config configs/scenarios_vsm_gfm.yaml` | All 23 GFM scenarios fully testable |

## Appendix: things we tried and failed

1. `typhoon.test.capture.start_capture(duration=0.1, signals=[...])` + `get_capture_results(wait_capture=True)` — infinite hang
2. Same with `wait_capture=False` — `Exception: There is no data in capture buffer`
3. `hil.start_capture(cpSettings=..., trSettings=['Forced'], chSettings=..., fileName=out)` — `capture_in_progress()` stuck True forever
4. `hil.start_capture(... dataBuffer=buf)` — buf stays empty
5. `hil.capture_signal(...)` — doesn't exist in this API version
6. `hil.read_analog_signals(signals=[...])` batch read — hangs
7. `read_analog_signal` polling — works but 4 Hz cap, aliases AC signals

If anyone reading this finds the magic combination that works, please
update this doc and `src/tools/hil_tools.py::_capture_typhoon`.
