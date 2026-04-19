# Scenario Library Index

THAA ships 3 predefined scenario libraries plus a fault-template engine you can
mix into custom YAMLs. Each scenario maps a power-electronics test to one or
more international standards.

## How scenarios are loaded

```
configs/<name>.yaml  →  src/nodes/plan_tests.py::_load_predefined_scenarios()
                     →  AgentState.scenarios[]
                     →  execute_scenario  →  evaluate  →  next / fail-loop
```

If the config YAML contains a `scenarios:` block, it's loaded **directly**
(no Claude Planner call). Otherwise, Claude Planner generates a plan from the
natural-language goal.

## Library 1 — `scenarios.yaml`  (BMS 12S Pack)

Original demo library. 10 scenarios for battery management protection.

| Scenario | Category | Standard |
|----------|----------|----------|
| `ovp_single_cell` / `ovp_boundary` / `ovp_multi_cell` | protection | IEC 62619 §7.2.1 |
| `uvp_single_cell` | protection | IEC 62619 |
| `ocp_pack` | protection | IEC 62619 |
| `lvrt_basic` | grid_compliance | IEC 61727 / KS C 8564 |
| `boost_regulation` | control_performance | — |
| `ovp_template_demo` | protection | IEEE 1547 §4.4.2 |
| `freq_deviation_template_demo` | grid_support | IEEE 1547 §6.5 |
| `sensor_disconnect` | fault_tolerance | — |

Run: `python main.py --goal "BMS protection sweep"`

## Library 2 — `scenarios_250123.yaml`  (ESS/EV Charger)

Generated from `250123_Test.tse` analysis. 32 scenarios across 13 categories.

| Standard | Section | Scenarios |
|----------|---------|-----------|
| **IEEE 1547-2018** | §4.4.2 Voltage trips | 5 (OV2/OV1/UV1/UV2/UV3) |
| **IEEE 1547-2018** | §6.5 Frequency trips | 4 (OF2/OF1/UF1/UF2) |
| **IEEE 1547-2018** | §6.4 VRT | 3 (LVRT deep / moderate, HVRT) |
| **IEEE 1547-2018** | §7.4 Power quality | 2 (THD rated / partial) |
| **IEEE 1547-2018** | §8.2 Anti-islanding | 2 (0% / 5% mismatch) |
| **IEC 62619 / UL 9540** | §40 DC link OVP/UVP/Battery | 3 |
| **IEC 62619 / IEEE 1547** | OCP | 2 (AC phase, DC link) |
| **IEC 62955 / IEC 61851-1** | §6.3.3 RCD | 1 (5 mA trip) |
| **UL 9540** | §38 Thermal | 3 (PFC/DCDC/heatsink) |
| **IEC 61851-1** | §11 Contactor | 2 (precharge, welding detect) |
| **UL 9540** | §42 Lock-out | 2 (AC, DC) |
| **UL 9540** | §36 SMPS fault | 1 |
| **Control performance** | §7.2 / §6.3 | 2 (DC link reg, CC-CV) |

Run: `python main.py --goal "ESS/EV charger compliance" --config configs/scenarios_250123.yaml`

## Library 3 — `scenarios_vsm_gfm.yaml`  (VSM Inverter — GFM)

Generated from `invertertest.tse` (Virtual Synchronous Machine). 23 scenarios.

| IEEE 2800-2022 Section | Topic | Scenarios |
|------------------------|-------|-----------|
| **§9** | Voltage source behavior | 5 (RMS, P tracking ×3, weak grid) |
| **§7.2.2** | Synthetic inertia | 4 (J=0.1/0.3/0.8, ROCOF) |
| **§7.4** | Fast Fault Current Injection (FFCI) | 4 (response, capped, sustained) |
| **§7.2.1** | Frequency support (P-f droop) | 5 (under/over freq ×2 each, response time) |
| **§7.3** | Phase jump (≤25°) | 3 (10°/20°/25°) |
| **§7.5** | Harmonic damping | 2 (V THD, I THD) |

Run: `python main.py --goal "IEEE 2800 GFM compliance" --config configs/scenarios_vsm_gfm.yaml`

The companion **pytest** project at `test_project_vsm_gfm/` holds 28 IEEE 2800
unit tests that run on real Typhoon HIL hardware (or VHIL) — see its README.

## Writing a new scenario

```yaml
my_scenario_id:
  description: "Human-readable summary"
  category: "protection | grid_compliance | grid_support | power_quality | control_performance | fault_tolerance"
  standard_ref: "IEEE 1547-2018 §X.Y"     # appears in HTML/Xray report
  parameters:
    fault_template: "overvoltage"         # one of 10 templates (or omit for legacy keys)
    signal_ac_sources: ["Vsa", "Vsb", "Vsc"]   # 3-phase
    nominal_voltage_peak: 325.27
    fault_voltage_pu: 1.22
    ramp_duration_s: 0.01
    hold_after_s: 0.5
  measurements:
    - "VACGRID_L1_"
    - "LOCK_OUT_AC"
  pass_fail_rules:
    lockout_must_trip: true
    clearing_time_max_s: 0.16
```

See `src/fault_templates.py` for required parameters per template and
`src/nodes/execute_scenario.py::_evaluate()` for available pass/fail rules.

## Adding a new standard

1. Add scenarios to a new `configs/scenarios_<domain>.yaml`
2. (Optional) Add new fault templates to `src/fault_templates.py` if existing
   ones don't cover the stimulus pattern.
3. (Optional) Extend `src/validator.py::writable_xcp_params` if the
   self-healing loop needs to retune new calibrations.
4. Update this index.
