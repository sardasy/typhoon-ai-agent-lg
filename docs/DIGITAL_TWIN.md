# Digital Twin (Phase 4-C MVP)

THAA can run a software mirror of the ECU's calibration state and use
it to vet calibration writes BEFORE they hit real hardware. The twin
catches three classes of clearly-bad fixes that would otherwise burn a
heal retry on the live system:

| Veto class | Example |
|------------|---------|
| **No-op fix** | Twin already shows `J = 0.35`. Analyzer suggests `J = 0.35` again. |
| **Out-of-range** | Analyzer suggests `J = 99.0`; IEEE 2800 inertia is ~0.05..2.0 s. |
| **Wrong-direction** | Failing rule `relay_must_trip` requires INCREASING `J`, but the proposal decreases it. |

The twin is **conservative**: when a parameter has no rule it knows
about, the verdict is `commit` (twin defers to live test). The only
blocking outcome is `veto`.

## Topology

When `--twin` is on, `simulate_fix` is inserted between
`analyze_failure` and `apply_fix`:

```
analyze_failure -> [route_after_analysis]
                   retry    -> simulate_fix -> [route_after_simulation]
                                                commit -> apply_fix
                                                veto   -> advance_scenario
                   escalate -> advance_scenario
```

`commit` and `uncertain` both proceed to `apply_fix`. `veto` skips
the write entirely; the scenario is escalated.

## Predictor

`src/twin.py::DigitalTwin.predict(scenario, failed_result, action)`
runs four checks in order:

1. **No-op fix.** `state[param] == proposed_value` -> veto.
2. **Repeat attempt.** History of writes for this `scenario_id`
   contains `(param, value)` already -> veto.
3. **Out-of-range.** `param` has an entry in `PLAUSIBLE_RANGES` and
   `value` is outside the band -> veto.
4. **Wrong-direction.** `param` + a rule from
   `scenario.pass_fail_rules` has an entry in `EFFECT_DIRECTION`,
   and the proposed delta has the wrong sign -> veto.

Otherwise: `commit` with a short explanation.

### Plausible ranges (`PLAUSIBLE_RANGES`)

| Param | Range | Source |
|-------|-------|--------|
| `J` (virtual inertia) | 0.01..5.0 s | IEEE 2800 |
| `D` (damping) | 0.0..50.0 | IEEE 2800 |
| `Kv` (voltage droop) | 0.0..5.0 | IEEE 2800 |
| `Ctrl_Kp` | 1e-4..1e3 | PI controller convention |
| `Ctrl_Ki` | 1e-4..1e3 | PI controller convention |
| `Ctrl_Kd` | 0.0..1e3 | PID controller convention |
| `BMS_OVP_threshold` | 3.5..4.5 V | IEC 62619 |
| `BMS_UVP_threshold` | 2.0..3.5 V | IEC 62619 |

### Sign-of-effect rules (`EFFECT_DIRECTION`)

Conservative -- only listed (param, rule) pairs vote on direction.
Add new entries when you have a confident heuristic, leave a pair out
when you don't.

| (Param, Rule) | Wants |
|---------------|-------|
| `(J, relay_must_trip)` | increase |
| `(J, rocof_max_hz_per_s)` | increase |
| `(D, overshoot_max_percent)` | increase |
| `(D, settling_time_max_ms)` | decrease |
| `(Kv, steady_state_error_max_percent)` | increase |
| `(BMS_OVP_threshold, relay_must_trip)` | decrease |
| `(Ctrl_Kp, rise_time_max_ms)` | increase |
| `(Ctrl_Kp, overshoot_max_percent)` | decrease |

## Running

```bash
# Single-agent + twin
python main.py --goal "VSM heal" --config configs/scenarios_heal_demo.yaml --twin

# Multi-agent + twin
python main.py --goal "ESS regression" --config configs/scenarios_250123.yaml \
  --orchestrator --twin

# All Phase 4 features together
python main.py --goal "..." --config configs/scenarios_vsm_gfm.yaml \
  --dut-backend hybrid --a2l-path firmware.a2l \
  --orchestrator --twin
```

The twin singleton is reset at the start of every CLI run so prior
state doesn't bleed between invocations. Within one run it
accumulates state from successful `apply_fix` calls.

## State

Two new `AgentState` fields:

- `twin_enabled: bool` -- controls whether `apply_fix` updates the
  twin's calibration mirror after a successful write.
- `twin_prediction: dict | None` -- last prediction's `to_dict()`,
  written by `simulate_fix`, read by `route_after_simulation`.

The twin singleton itself lives in `src.twin` (one per process). Use
`reset_twin()` to drop accumulated state between independent runs.

## Tests

```bash
python -m pytest tests/test_twin.py -v
# 21 tests:
#   verdicts:        no-op, repeat, out-of-range, wrong-direction veto;
#                    in-range / direction commit; uncertain on missing value
#   plausible bands: every writable XCP param has a sane range
#   graph:           default has no simulate_fix; --twin inserts it;
#                    orchestrator + twin works
#   simulate_fix:    node emits prediction; vetoes no-ops; commits clean
#   singleton:       get_twin / reset_twin
```

## Out of scope (future)

- High-fidelity simulation. The twin is a sanity gate, not a plant
  model. For "what would the waveform look like with `J = 0.4`?",
  use the HIL with `Hybrid` DUT backend instead.
- Learning the `EFFECT_DIRECTION` table from prior runs. Currently
  hand-curated.
- Rolling history persistence. The twin resets every CLI run; for
  cross-run "we already tried this calibration" the right place is
  the SQLite checkpointer.
