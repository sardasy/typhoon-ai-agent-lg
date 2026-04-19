# Scenario Evaluator

## Why this exists

Before this change, `execute_scenario._evaluate()` hard-coded 4 rules
(`relay_must_trip`, `relay_must_not_trip`, `response_time_max_ms`,
`overshoot_max_percent`, `steady_state_error_max_percent`). Any
`pass_fail_rules` key that wasn't in that list was silently ignored and
the scenario returned **PASS**.

The scenario YAMLs, however, use **68 distinct rule keys**. So a scenario
claiming IEEE 2800 compliance (FFCI response ≤ 20 ms, ROCOF bound,
THD ≤ 5%) could pass without any of those rules ever running.

## What changed

`src/evaluator.py` replaces the hard-coded logic with a rule dispatcher.

- Each rule is a handler that returns `(status, reason)` where status is
  `pass` / `fail` / `error` / `None` (skip).
- Handlers are registered via `@register("rule_name")`; one handler may
  be registered for multiple synonymous keys.
- Synonyms such as `_pct` / `_percent` and `_max_s` / `_max_ms` are
  resolved via an `_ALIASES` map.
- **Strict mode** (default): an unknown rule key produces `error` — so
  typos and outdated scenarios surface immediately instead of silently
  passing.

## Rule families (current coverage)

| Family | Examples | Notes |
|--------|----------|-------|
| Digital / protection | `relay_must_trip`, `lockout_must_trip`, `both_lockouts_must_trip`, `all_contactors_must_open`, `must_stay_connected`, `must_not_trip`, `igbt_gate_must_block`, `battery_current_must_zero` | Looks for signal names containing "relay"/"trip"/"lock"/"contactor" |
| Timing | `response_time_max_ms`, `clearing_time_max_s`, `detection_time_max_ms`, `island_detection_max_s`, `settling_time_max_s/ms`, `ffci_response_max_ms`, `stability_recovery_s` | Uses `WaveformStats.rise_time_ms` / `settling_time_ms`; returns ERROR when capture didn't record them (i.e. non-triggered capture) |
| Voltage thresholds | `voltage_threshold_pu` (+ `tolerance_pu`), `dc_link_voltage_max_pu`, `min_voltage_pu`, `voltage_threshold` | per-unit uses `scenario.parameters.nominal_voltage_peak` as base |
| Current / power | `max_overcurrent_pu`, `ffci_current_min_pu`, `ffci_current_max_pu`, `inrush_current_max_pu`, `power_tolerance_percent` (+ `target_p_w`), `current_peak_max_pu` | Base current derived from `Pref_w / (sqrt(3) * V_ll)` |
| Quality | `voltage_thd_max_pct`, `current_thd_max_pct`, `dc_injection_max_pct`, `dc_link_ripple_max`, `individual_harmonic_limits` | Requires `WaveformStats.thd_percent` to be populated by capture |
| Frequency | `rocof_max_hz_per_s`, `frequency_threshold_hz` | Requires `WaveformStats.rocof_hz_per_s`; threshold check informational (no freq probe in stats) |
| Transient | `overshoot_max_percent`, `steady_state_error_max_percent`, `voltage_regulation_error_max_percent`, `current_limit_overshoot_max_percent`, `dc_link_overshoot_max_percent` | |
| Directional | `direction` + `min_change_percent` | Informational (no before/after capture yet) |

`src.evaluator.registered_rules()` returns the full list. Over 60 rule
keys are now handled.

## WaveformStats extensions

`src/state.py::WaveformStats` gained two optional fields so capture can
surface derived metrics for the evaluator:

```python
class WaveformStats(BaseModel):
    ...
    thd_percent: float | None = None
    rocof_hz_per_s: float | None = None
```

When a scenario needs THD / ROCOF it should request them via
`analysis: ["mean", "rms", "thd", "rocof"]` in the capture call.
`_capture` currently doesn't compute these on VHIL — **scenarios asking
for THD / ROCOF now correctly ERROR out** instead of falsely passing.
That's a separate implementation task (VHIL needs higher-rate capture
first).

## Honest impact

Before vs. after on the same commit, same mock environment:

```
configs/scenarios_vsm_gfm.yaml
  before: 23 PASS / 0 FAIL / 0 ERROR   (evaluator ignored 55 rule keys)
  after :  5 PASS / 13 FAIL / 10 ERROR (5 real passes, 13 genuine mock
                                        failures, 10 "capture can't
                                        answer this rule")

configs/scenarios.yaml
  before: 10 PASS / 0 FAIL / 0 ERROR
  after :  1 PASS / 6 FAIL / 3 ERROR
```

The "fewer PASSes" is the point: the system now tells the truth about
what is and isn't verified in mock / VHIL mode.

## How to add a new rule

```python
from src.evaluator import register

@register("my_new_rule")
def _my_new_rule(val, rules, stats_map, scenario):
    sig = stats_map.get("MySignal")
    if sig is None:
        return "error", "MySignal not captured"
    if sig.max > val:
        return "fail", f"MySignal peaked at {sig.max} > {val}"
    return "pass", ""
```

Register under an alias when the rule has synonyms:

```python
_ALIASES["my_rule_alt_name"] = "my_new_rule"
```

## Strict vs lenient

```python
from src.evaluator import evaluate

# Default: unknown keys -> ERROR
evaluate(rules, stats, scenario=scenario, strict=True)

# Tolerant mode for migration scripts, ad-hoc tests:
evaluate(rules, stats, scenario=scenario, strict=False)
```

`execute_scenario` calls `strict=True` in production so drift between
YAML and code is visible.

## Tests

`tests/test_evaluator.py` (26 tests) covers every rule family and the
strict mode guard. Combined with the existing suite the project now runs
**143 mock-mode tests**.
