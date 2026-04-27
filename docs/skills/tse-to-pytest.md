---
description: Parse a Typhoon HIL .tse file and generate a complete pytest test project using typhoon.test.* high-level APIs.
argument-hint: <path-to-tse-file>
allowed-tools: Read, Write, Bash, WebFetch, mcp__Context7__get-library-docs
---

# /tse-to-pytest

Mission: Convert the `.tse` schematic file at `$ARGUMENTS` into a complete pytest project.

If `$ARGUMENTS` is empty, ask the user to provide the path or upload the file.

---

## Hard Rules (NEVER violate)

### R1. Use ONLY `typhoon.test.*` high-level APIs

| Domain | Required API | Forbidden |
|--------|-------------|-----------|
| Model load | `from typhoon.api.schematic_editor import model` then `model.load()`, `model.compile()` | `SchematicAPI()` direct |
| CPD path | `model.get_compiled_model_file(MODEL_PATH)` | manual `.tse→.cpd` rename |
| Capture | `from typhoon.test import capture` then `capture.start_capture(duration=, rate=, signals=)` | `hil.start_capture()` |
| Capture results | `capture.get_capture_results(wait_capture=True)` | `dataBuffer` polling |
| Signal assert | `signals.assert_is_constant()`, `signals.assert_is_first_order()` | manual `abs(v - ref) < tol` |
| Tolerance | `from typhoon.test.ranges import around` then `around(50, tol_p=0.02)` | manual tolerance math |
| Reporting | `import typhoon.test.reporting.messages as report` then `report.report_message()` | `print()` |
| Timing | `hil.wait_sec(0.5)` | `time.sleep(0.5)` |

### R2. Capture DataFrame index is `timedelta64[ns]`

```python
# WRONG — TypeError
response[response.index >= 0.5]

# RIGHT
import pandas as pd
response[response.index >= pd.Timedelta(seconds=0.5)]

# Helper (always include in constants.py)
def ts(seconds: float) -> pd.Timedelta:
    return pd.Timedelta(seconds=seconds)
```

`signals.assert_is_constant(during=[0.5, 1.5])` accepts floats (internal conversion). Only manual slicing requires `pd.Timedelta`.

### R3. Signal names have NO subsystem prefix

- ✅ `"Vout"`, `"Iin"`, `"PWM Enable"`
- ❌ `"My model.Vout"`

### R4. Fixture pattern (strict)

```
setup            (scope="module")   → model.load + model.compile + hil.load_model
reset_parameters (scope="function") → restore SCADA / source defaults
inside each test                    → hil.start_simulation / hil.stop_simulation
```

- ❌ NO `sim_running`, `sim_with_pwm` integrated fixtures
- ❌ NO `hil.connect()` / `hil.disconnect()`
- ❌ NO `scope="session"` (use `module`)

### R5. ASCII-only Python files (Windows cp1254 constraint)

All generated `.py` MUST be pure ASCII. Korean comments → put them in companion `.md` files instead.

---

## Workflow

### Step 0: Fetch latest API docs (parallel)

Run these two in parallel before code generation:

1. **pytest docs via Context7:**
   ```
   mcp__Context7__get-library-docs(
       context7CompatibleLibraryID="/websites/pytest_en_stable",
       topic="<topic by topology, see table below>",
       tokens=5000
   )
   ```

2. **Typhoon HIL API docs via WebFetch:**
   - `https://www.typhoon-hil.com/documentation/typhoon-hil-api-documentation/hil_api.html`
   - `https://www.typhoon-hil.com/documentation/typhoon-hil-api-documentation/test_api.html`

Topic keywords by topology:
| Topology | topic |
|----------|-------|
| DC-DC converter | `fixtures parametrize markers conftest scope module function` |
| 3-phase inverter / ESS | `fixtures parametrize markers conftest class grouping scope` |
| DPT / switching | `fixtures parametrize indirect marks timeout` |
| BMS / battery | `fixtures parametrize timeout markers xfail skip` |
| Protection-heavy | `fixtures parametrize xfail skip markers expected failures` |

If fetch fails → use built-in rules below. If external doc conflicts with built-in rule → **built-in rule wins** (Typhoon-specific overrides).

### Step 1: Detect .tse format

```bash
head -1 "$ARGUMENTS"
```

- `version = 4.2` → text format (2026.1+) → direct text parsing
- `<?xml` → XML format → `scripts/extract_tse_params.py`

### Step 2: Extract parameters

For text format, parse blocks for:
- `component "core/Voltage Measurement" <name>` → analog signal (V)
- `component "core/Current Measurement" <name>` → analog signal (I)
- `component "core/Probe" <name>` → general probe
- `component "core/Digital Probe" <name>` → digital signal
- `component "core/Voltage Source" <name>` → source, read `init_const_value`
- `component "core/SCADA Input" <name>` → SCADA, read `def_value`
- `component "core/Boost"|"core/Three Phase Inverter"|"core/H Bridge"` → topology hint
- `configuration` block → `simulation_time_step`, `dsp_timer_periods`
- `CODE model_init` block → control params (Kp, Ki, Ts...)

### Step 3: Confirm extraction with user

Print a brief summary then ask before generating:

```
TSE parsed: <topology>
Capture signals: <list>
Sources / SCADA: <list>
Test params: Kp=..., Ki=..., fsw=...kHz
Doc fetch: pytest=<ok|fail>, typhoon=<ok|fail>

Generate test code? (y/n)
```

### Step 4: Generate project

Output to `/mnt/user-data/outputs/<tse_stem>/`:

```
<tse_stem>/
├── conftest.py
├── constants.py
├── pytest.ini
├── tests/
│   ├── test_<topology>.py
│   └── ...
└── utils/
    └── analysis.py
```

---

## Templates

### `constants.py` (always include `ts()` helper)

```python
# -*- coding: utf-8 -*-
import os
import pandas as pd
from pathlib import Path
from typhoon.api.schematic_editor import model

def ts(seconds: float) -> pd.Timedelta:
    return pd.Timedelta(seconds=seconds)

FILE_DIR_PATH = Path(__file__).parent
MODEL_PATH = os.path.join(FILE_DIR_PATH, "..", "..", "models",
                          "<category>", "<model>", "<model>.tse")
COMPILED_MODEL_PATH = model.get_compiled_model_file(MODEL_PATH)

# Electrical
VIN_DC_V         = 35.0
VOUT_REFERENCE_V = 50.0

# Tolerance (around() uses ratio for tol_p)
VOLTAGE_TOL_PCT     = 0.02   # 2% -> around(50, tol_p=0.02)
VOLTAGE_TOL_STRICT  = 0.75
TIME_CONSTANT_S     = 0.10
TIME_TOL_S          = 20e-3

# Capture
CAPTURE_RATE_HZ     = 10e3
CAPTURE_DURATION_S  = 1.5
SETTLE_TIME_S       = 0.5

# Signals (no subsystem prefix)
ANALOG_SIGNALS  = ["Vout", "Iin"]
SOURCE_VIN      = "Vin"
SCADA_REFERENCE = "Reference"
SCADA_PWM_ENABLE = "PWM Enable"
```

### `conftest.py` (official pattern)

```python
# -*- coding: utf-8 -*-
import pytest
from typhoon.api import hil
from typhoon.api.schematic_editor import model
import typhoon.test.reporting.messages as report
from constants import *

@pytest.fixture(scope="module")
def setup():
    """Load + compile + load to VHIL."""
    report.report_message("Virtual HIL device is used.")
    model.load(MODEL_PATH)
    model.compile(conditional_compile=True)
    hil.load_model(COMPILED_MODEL_PATH, vhil_device=True)

@pytest.fixture()
def reset_parameters():
    """Restore SCADA + source defaults before each test."""
    hil.set_scada_input_value(SCADA_PWM_ENABLE, 1.0)
    hil.set_scada_input_value(SCADA_REFERENCE, VOUT_REFERENCE_V)
    hil.set_source_constant_value(SOURCE_VIN, value=VIN_DC_V)
```

### Test pattern (canonical)

```python
import pytest
from typhoon.api import hil
from typhoon.test import capture, signals
from typhoon.test.ranges import around
import typhoon.test.reporting.messages as report
from constants import *

@pytest.mark.parametrize("vin_dist", [0.80, 1.10])
def test_disturbance_vin(setup, reset_parameters, vin_dist):
    """Output regulation under Vin disturbance."""
    capture.start_capture(
        duration=CAPTURE_DURATION_S,
        rate=CAPTURE_RATE_HZ,
        signals=ANALOG_SIGNALS,
    )
    hil.start_simulation()
    hil.wait_sec(SETTLE_TIME_S)
    hil.set_source_constant_value(SOURCE_VIN, value=VIN_DC_V * vin_dist)

    df = capture.get_capture_results(wait_capture=True)
    hil.stop_simulation()

    response = df["Vout"]
    signals.assert_is_constant(
        response,
        around(VOUT_REFERENCE_V, tol_p=VOLTAGE_TOL_PCT),
        during=[SETTLE_TIME_S, CAPTURE_DURATION_S],
        strictness=VOLTAGE_TOL_STRICT,
    )
```

### Topology test menu

| Topology | Test cases |
|----------|-----------|
| DC-DC Boost/Buck | `test_disturbance_vin`, `test_reference_tracking` (assert_is_first_order), `test_pwm_disable`, `test_extreme_vin` |
| 3-phase inverter / ESS / PCS | DC-link constancy, RMS per phase, THD via FFT, contactor load step |
| DPT | turn-on/off time, dV/dt, dI/dt |
| BMS | OVP/OCP trigger, SOC range, charge/discharge |

### `utils/analysis.py` (supplements `typhoon.test.signals`)

```python
import numpy as np

def calc_ripple_pp(series) -> float:
    arr = np.asarray(series)
    return float(np.max(arr) - np.min(arr))

def calc_ripple_pct(series, nominal: float) -> float:
    if nominal == 0:
        return 0.0
    return calc_ripple_pp(series) / abs(nominal) * 100.0

def calc_thd(arr, fundamental_hz, sample_rate_hz, max_harmonics=10):
    arr = np.asarray(arr)
    n = len(arr)
    fft_mag = np.abs(np.fft.rfft(arr)) / n * 2
    bin_res = sample_rate_hz / n
    f1_idx = int(round(fundamental_hz / bin_res))
    fundamental = fft_mag[f1_idx] if f1_idx < len(fft_mag) else 1.0
    harmonics_sq = sum(
        fft_mag[min(f1_idx * k, len(fft_mag) - 1)] ** 2
        for k in range(2, max_harmonics + 1)
    )
    return float(np.sqrt(harmonics_sq) / fundamental * 100)

def calc_efficiency(v_in, i_in, v_out, i_out):
    p_in = v_in * i_in
    if p_in <= 0:
        return 0.0
    return float(v_out * i_out / p_in * 100.0)
```

---

## Step 5: Validate generated code

Before finalizing, run an ASCII check on every generated `.py`:

```bash
python -c "import pathlib, sys; \
[pathlib.Path(p).read_bytes().decode('ascii') for p in sys.argv[1:]]" \
<list of generated .py files>
```

Exit non-zero → reject and regenerate. Korean characters belong in `.md`, not `.py`.

## Step 6: Hand off

Brief the user with:
- Run command: `pytest tests/ -v`
- `MODEL_PATH` likely needs adjustment to actual location
- Doc fetch status (Context7 / WebFetch ok or fallback)
- Any signal name ambiguities the user should verify
