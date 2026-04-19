# Self-Healing Loop Demo

End-to-end demonstration of the LangGraph self-healing path:

```
execute_scenario  ─FAIL─▶  analyze_failure (Claude)
        ▲                          │
        │                          ▼ diagnosis: xcp_calibration
        └─ retry  ◀── apply_fix (XCP write)
```

## Run

```bash
python main.py --goal "VSM inverter inertia tuning failure -- diagnose and retune J via XCP" \
               --config configs/scenarios_heal_demo.yaml
```

Requires `ANTHROPIC_API_KEY`. No Typhoon HIL hardware needed (uses mock).

## What you should see

```
[01][load_model       ] Device mode: vhil_mock
[02][plan_tests       ] Loaded 1 predefined scenarios
[03][execute_scenario ] FAIL: VSM tuning failure ... — Protection relay did not trip
        >>> vsm_inertia_heal_demo retry#0 = FAIL | Protection relay did not trip
[04][analyze_failure  ] Root cause: VSM inertia constant J=0.05 is too low ...
        DIAG action=xcp_calibration, param=J, value=0.35, conf=92%
[05][apply_fix        ] XCP write: J = 0.35 (retry #1)
[06][execute_scenario ] PASS: VSM tuning failure ...
        >>> vsm_inertia_heal_demo retry#1 = PASS
[07][generate_report  ] Total: 2, Passed: 1, Failed: 1, Rate: 50.0%
```

The report records two scenario executions: the failed first attempt and the
healed second attempt. The "50% pass rate" is expected — both runs are kept
in `results[]` for full audit trail.

## How the mock converges

The retry-aware mock lives in `src/tools/hil_tools.py::_capture` (else branch):

1. The scenario YAML declares `parameters.heal_target_param: "J"` and
   `parameters.heal_target_threshold: 0.3`.
2. `execute_scenario` forwards those keys into the `hil_capture` tool
   call (alongside `signals`, `duration_s`, etc.).
3. `apply_fix` calls XCP write, which records the new value in
   `src/tools/xcp_tools.py::LAST_XCP_WRITE`.
4. On the next mock capture, the executor checks
   `LAST_XCP_WRITE["J"] >= 0.3`. When True, it returns waveform stats with
   `relay max=1.0`, `rise_time_ms=50`, satisfying `relay_must_trip` and
   `response_time_max_ms` rules.

This simulates real-hardware convergence: the controller actually starts
oscillating once J is large enough, the relay trips, the test passes.

## On real hardware

The same scenario would run on real Typhoon HIL or VHIL with no code
changes — `LAST_XCP_WRITE` is a mock-only convenience. On real hardware:

1. apply_fix writes `J=0.35` via XCP to the actual ECU calibration.
2. The VSM controller responds with new dynamics.
3. The next capture window observes real oscillation → relay trips → test
   passes (or doesn't, in which case Claude tries again).

The agent always honours `MAX_HEAL_RETRIES = 3`. If three retries fail the
graph escalates via `route_after_analysis` and moves on to the next
scenario.

## Customising

To make your own scenario heal-aware:

```yaml
my_failing_scenario:
  parameters:
    fault_template: "vsm_steady_state"
    Pref_w: 5000.0
    J: 0.05                          # bad value -> failure
    heal_target_param: "J"           # what apply_fix should write
    heal_target_threshold: 0.3       # mock pass threshold
  measurements:
    - "VSM_oscillation_relay"        # any signal name with 'relay' or 'trip'
  pass_fail_rules:
    relay_must_trip: true
```

The Claude Analyzer prompt is generic enough to recognise "calibration
constant too low" patterns; if you want more reliable suggestions for
domain-specific parameters, extend `prompts/analyzer.md` with VSM /
GFM-specific guidance.
