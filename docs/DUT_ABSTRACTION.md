# DUT Abstraction Layer (Phase 4 MVP)

THAA's `execute_scenario` and `apply_fix` nodes now go through a
backend-neutral interface (`DUTBackend`). The same scenario YAML can run
against the Typhoon HIL, a real ECU via pyXCP, a HIL+ECU hybrid, or a
mock — chosen at run-time, no scenario rewrite needed.

## Backends

| Backend | Stimulus | Capture | Calibration | Use case |
|---------|----------|---------|-------------|----------|
| `hil` *(default)* | HIL source | typhoon.test.capture | HIL SCADA / source | Current behavior — HIL only |
| `xcp` | NotImplementedError | **pyXCP DAQ** *(Phase 4-E)* | pyXCP write | Real ECU calibration + ECU-internal DAQ capture |
| `hybrid` | HIL source | HIL capture | pyXCP write | **Recommended for real bring-up:** HIL = plant, real ECU = DUT |
| `mock` | Records call | Synthetic stats | LAST_XCP_WRITE | Tests |

## Selecting a backend

### CLI

```bash
# default — same as before
python main.py --goal "VSM heal demo" --config configs/scenarios_heal_demo.yaml

# real ECU + HIL plant (recommended hardware setup)
python main.py --goal "..." --config configs/scenarios_vsm_gfm.yaml \
  --dut-backend hybrid --a2l-path firmware/v1.2.a2l

# calibration-only (no stimulus)
python main.py --goal "tune Kp" --dut-backend xcp --a2l-path firmware/v1.2.a2l
```

### Environment / programmatic

`AgentState` carries `dut_backend` (str) and `dut_config` (dict).
`make_initial_state()` accepts both as kwargs:

```python
from main import make_initial_state
state = make_initial_state(
    "VSM heal", config_path="configs/scenarios_heal_demo.yaml",
    dut_backend="hybrid",
    dut_config={"a2l_path": "firmware/v1.2.a2l"},
)
```

The graph topology is unchanged — only the singleton wiring inside
`load_model`, `execute_scenario`, `apply_fix` was refactored.

## Adding a new backend

1. Create `src/tools/dut/<name>_backend.py` with a class inheriting
   `BaseBackend` and implementing the abstract methods (`control`,
   `write_signal`, `read_signal`, `capture`, `fault_inject`,
   `write_calibration`, `read_calibration`).
2. Register in `src/tools/dut/__init__.py::_BACKEND_REGISTRY`.
3. Add to `--dut-backend` choices in `main.py`.
4. Add contract tests in `tests/test_dut_backend.py` mirroring the
   existing patterns (factory, behavior, get_dut integration).

The default `BaseBackend.execute()` shim translates legacy
`execute("hil_signal_write", {...})` calls to typed methods, so existing
fault templates and tests keep working without modification.

## Safety

- `XCPBackend.write_calibration` runs through the same
  `XCPToolExecutor.WRITABLE_PARAMS` whitelist as before — non-listed
  parameters are blocked.
- `HILBackend.write_calibration` only succeeds for signals that map to a
  HIL SCADA input or source. Anything else returns an
  `unsupported: True` error so `apply_fix` logs it instead of falsely
  succeeding.
- The `Validator` safety gate in `apply_fix.py` is unchanged.

## Migration notes

- `from src.nodes.load_model import get_hil` still works (returns the
  underlying `HILToolExecutor` of the cached HIL backend).
- New code should call `get_dut(state)` instead. The returned object has
  typed methods *and* an `execute()` shim, so it's a drop-in for old
  call sites.
- Existing scenario YAML files need no changes.

## XCP DAQ capture (Phase 4-E)

`XCPBackend.capture()` now performs DAQ-based waveform capture:

- **Real path** (`HAS_XCP=True` + connected master + a2l loaded):
  allocates one DAQ list with one ODT per signal, calls
  `startStopSynch(0x01)`, sleeps the duration, stops with
  `startStopSynch(0x00)`, then drains samples per signal. Stats
  (mean, max, min, rms, rise_time, overshoot) computed in Python.
- **Mock path**: synthesizes per-signal time series. Mirrors the HIL
  mock's heal-target convergence trick — when
  `LAST_XCP_WRITE[heal_target_param] >= heal_target_threshold`,
  relay-like signals jump to 1.0 (so `--dut-backend xcp` self-heal
  demos converge without an ECU on the bench).

Stat shape matches `HILBackend.capture` so the evaluator works
unchanged regardless of backend. Output includes a `source` field
(`"xcp_daq"` or `"xcp_mock"`) so logs are unambiguous.

```bash
# XCP-only run (calibration + capture, no HIL)
python main.py --goal "tune Kp on bench ECU" \
  --config configs/scenarios_heal_demo.yaml \
  --dut-backend xcp --a2l-path firmware.a2l
```

## Multi-device routing (Phase 4-I)

Backend instances are keyed by `(name, device_id)`. Each device gets
its own `asyncio.Lock` (`get_hardware_lock(device_id)`), so I/O on
different devices runs concurrently while same-device calls
serialize. Scenarios opt in via a `device_id` YAML field (default
`"default"` keeps single-device behavior unchanged).

```yaml
scenarios:
  rig_a_test:
    device_id: hil_404_a
    parameters: {...}
  rig_b_test:
    device_id: hil_404_b
    parameters: {...}
```

`state["device_pool"]: dict[str, dict]` carries per-device DUT
config overlays (e.g. distinct A2L paths). `get_dut(state, scenario=s)`
merges the overlay on top of `state["dut_config"]` and returns the
cached backend instance for that device.

See `docs/REAL_TYPHOON_BRINGUP.md` "Multi-device HIL" for bring-up
details.

## Out of scope (future milestones)

- Real HIL404 + real ECU hardware verification (mock-only here)
- HITL inside the parallel orchestrator
