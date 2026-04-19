# IEEE 2800 GFM Compliance Test Suite — VSM Inverter

Pytest-based Hardware-in-the-Loop (HIL) compliance test suite for the
Virtual Synchronous Machine (VSM) inverter model `invertertest.tse`.
Verifies grid-forming (GFM) requirements per **IEEE 2800-2022**.

## Tested standards

| Section | Topic | Test file |
|---------|-------|-----------|
| 7.2.1 | Primary frequency response (P-f droop) | `test_frequency_support.py` |
| 7.2.2 | Synthetic / virtual inertia | `test_virtual_inertia.py` |
| 7.3 | Phase jump response (≤ 25°) | `test_phase_jump.py` |
| 7.4 | Fast Fault Current Injection (FFCI) | `test_fast_fault_current_injection.py` |
| 7.5 | Harmonic distortion limits | `test_harmonic_damping.py` |
| 9 | Voltage source behavior | `test_voltage_source_behavior.py` |

## Project layout

```
test_project_vsm_gfm/
├── config/
│   └── test_params.json        # IEEE 2800 thresholds + signal map
├── models/
│   └── model_builder.py        # Compiles invertertest.tse -> .cpd
├── tests/
│   ├── conftest.py             # HIL fixtures (session-scoped)
│   ├── test_runner.py          # Standalone bring-up runner
│   ├── test_voltage_source_behavior.py
│   ├── test_virtual_inertia.py
│   ├── test_fast_fault_current_injection.py
│   ├── test_frequency_support.py
│   ├── test_phase_jump.py
│   └── test_harmonic_damping.py
├── utils/
│   └── signal_analysis.py      # FFT, THD, RMS, step-response utils
├── pytest.ini
├── requirements.txt
└── README.md
```

## Prerequisites

1. **Typhoon HIL Control Center 2026.1 SP1** (or compatible) installed
2. The bundled Python on `PATH` (or use `typhoon_studio` venv)
3. The `.tse` file at the path declared in `config/test_params.json`

```bash
pip install -r requirements.txt
```

## Running

```bash
# Compile the model once
python models/model_builder.py

# Full IEEE 2800 GFM compliance suite
pytest

# Single category
pytest tests/test_virtual_inertia.py

# Only IEEE 2800 markers
pytest -m ieee2800

# HTML report
pytest --html=report.html --self-contained-html
```

### Virtual HIL vs real device

The session fixture chooses VHIL by default. Force real hardware with:

```bash
set THAA_USE_VHIL=0   # Windows
pytest
```

## Signal map (TSE → tests)

| Test signal | TSE component | Type |
|-------------|--------------|------|
| `Va`, `Vb`, `Vc` | Voltage Measurement | Phase voltages |
| `Vab`, `Vbc` | Voltage Measurement | Line voltages |
| `VDC` | Voltage Measurement | DC link |
| `Ia`, `Ib`, `Ic` | Current Measurement | Inverter currents |
| `Idc_link` | Current Measurement | DC current |
| `teta`, `w` | Probe | VSM phase angle / omega |
| `Te`, `Pe`, `Qe` | Probe | Electromagnetic torque, P, Q |
| `Pref`, `Qref` | SCADA Input | Power references |
| `J`, `D`, `Kv` | SCADA Input | VSM tuning (inertia, damping, droop) |
| `Vgrid` | Three Phase Voltage Source | Grid emulator |

## IEEE 2800 thresholds applied

All thresholds live in `config/test_params.json` so you can adjust without
touching test code. Defaults match IEEE 2800-2022:

- Phase jump max: **25°** with **1.0s** recovery
- FFCI response: **≤ 20 ms**, current **1.0–1.5 pu**
- Synthetic inertia H: **5–10 s**, settling **≤ 5 s**
- Frequency droop: **3–5 %**, deadband **36 mHz**
- THD voltage / current: **≤ 5 %**

## Integration with `typhoon_ai_agent_lg`

This suite plugs into the parent THAA project:

1. Add the project as a scenario YAML — wrap each test class as a scenario
   with `standard_ref: "IEEE 2800 §X.Y"`.
2. Run via the LangGraph agent:
   ```bash
   python ../main.py --goal "Run IEEE 2800 GFM suite for VSM inverter" \
                     --config configs/scenarios_vsm_gfm.yaml
   ```
3. The agent's `analyze_failure` node will diagnose any failures and the
   self-healing loop can retune VSM `J`/`D`/`Kv` automatically.
