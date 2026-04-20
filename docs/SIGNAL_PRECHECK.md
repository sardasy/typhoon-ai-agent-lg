# Signal-Name Pre-Check

Scenarios that reference signal names not present in the loaded HIL model
are now caught immediately in `plan_tests` and short-circuited to `error`
in `execute_scenario`, without running any stimulus or calling the Claude
Analyzer. This closes a class of slow/confusing failures that used to take
30-60 s per bad scenario (full stimulus + capture + analyzer retry loop).

## What gets validated

`src/signal_validator.py::required_signals()` extracts every signal the
scenario needs:

| Source | Example |
|--------|---------|
| `measurements: [...]` | `["Va", "BMS_OVP_relay"]` |
| `parameters.signal` | `"V_cell_1"` |
| `parameters.signal_ac_sources: [...]` | `["Vgrid", "Vsa", "Vsb"]` |
| `parameters.scada_input` / `scada_inputs: [...]` | `"P_ref"` |
| `parameters.target_sensor` / `breaker_signal` | `"V_cell_1"` |
| `parameters.contactor_sequence[*].signal` | `"AC_RLY_L1"` |

Tokens containing `{...}` templates or `$var` placeholders are skipped
— they're resolved later by stimulus code.

## Flow

```
load_model
  └─▶ state.model_signals = hil.get_analog_signals()   (18 in mock, 47 on VSM)

plan_tests
  ├─ load scenarios (YAML predefined OR Claude-planned)
  └─▶ signal_validator.attach_validation(scenarios, state.model_signals)
        └─ for each scenario:
            needed = required_signals(scenario)
            missing = needed - set(model_signals)
            if missing: scenario["validation_errors"] = [...]

execute_scenario (per scenario)
  └─ if scenario.get("validation_errors"):
        return status="error", reason="pre-check: signal not in model: ..."
        (no stimulus, no capture, no Claude call)
```

## Before vs after (3-scenario demo, `configs/scenarios_precheck_demo.yaml`)

```
Before:
  ovp_valid          FAIL  0.3 s
  ovp_typo           FAIL  0.3 s + Claude analyze (~11 s)
  ovp_hallucinated   FAIL  0.3 s + Claude analyze (~11 s)
  TOTAL              ~36 s

After:
  ovp_valid          FAIL  0.3 s
  ovp_typo           ERROR 0.0 s   (pre-check: signal not in model: ...)
  ovp_hallucinated   ERROR 0.0 s   (pre-check: signal not in model: ...)
  TOTAL              ~11 s          (~3x faster, 2 Claude calls saved)
```

## Cost savings

Per bad scenario avoided:
- ~30 s of stimulus + capture wall time
- 2 Claude API calls (analyze_failure, then escalate)
- 1 heal retry attempt

With the default 3-attempt heal loop, a scenario with a typo used to cost
up to 4 Claude analyses before giving up. Now it's zero.

## Integration points

- `src/signal_validator.py` — pure-function validator (100 lines, no external deps)
- `src/nodes/plan_tests.py` — calls `attach_validation()` for both YAML-loaded
  and Claude-planned scenarios; emits a `warning` event listing bad scenarios
- `src/nodes/execute_scenario.py` — checks `scenario["validation_errors"]`
  and returns an `error` ScenarioResult immediately if set

## Degraded mode: empty signal list

If `model_signals` is empty (for example when `hil.load_model` fails or
mock discovery is disabled), the validator **skips the check** entirely
rather than flag every scenario. This avoids false positives when signal
discovery itself is the problem.

## Tests

`tests/test_signal_validator.py` (15 tests) covers:
- Signal extraction from every scenario field
- Placeholder token skipping (`{target_cell}`, `$var`)
- Clean vs mixed vs all-bad scenarios
- Empty-model-signals degraded mode
- In-place annotation + stale-error removal

Total suite after this change: **175 tests**.
