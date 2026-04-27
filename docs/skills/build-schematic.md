---
description: Generate a Python script that uses Typhoon HIL SchematicAPI to programmatically build a .tse circuit model.
argument-hint: <topology> [voltage] [current] [control-type] [fsw]
allowed-tools: Read, Write, Bash, WebFetch
---

# /build-schematic

Mission: Produce a Python script that, when executed inside Typhoon HIL Control Center / TyphoonTest IDE, creates a `.tse` model file for the topology requested in `$ARGUMENTS`.

If `$ARGUMENTS` is empty or ambiguous, ask the user for: **topology, voltage spec, current spec, control type (PI/PID), switching frequency**.

---

## Hard Rules (NEVER violate)

### R1. ASCII-only Python output

TyphoonTest IDE loads files with Windows codepage (cp1254). One non-ASCII byte → loader crash.

- All comments in English only
- No special characters: use `u` for micro, `Ohm` for Ω, no checkmarks/arrows
- No f-strings with Unicode → use `.format()`
- First line: `# -*- coding: utf-8 -*-` (defensive only — keep content ASCII)

Verify before delivering:
```bash
python -c "import pathlib, sys; pathlib.Path(sys.argv[1]).read_bytes().decode('ascii')" <file>
```

### R2. Use pre-built converter blocks (NOT discrete IGBT+diode)

| Topology | Component | PE terminals | SP terminals |
|----------|-----------|--------------|--------------|
| Boost | `core/Boost` | a_in, a_out, b_in, b_out | En, In |
| Buck | `core/Buck` | a_in, a_out, b_in, b_out | En, In |
| H-Bridge | `core/H Bridge` | a_in, a_out, b_in, b_out | En, In1, In2 |
| 3φ Inverter | `core/Three Phase Inverter` | a_in, a_out, b_in, b_out, c_out | En, Ia, Ib, Ic |

These blocks contain Diode + IGBT + PWM modulator internally.
Set `ctrl_src = "Internal modulator"` to use built-in PWM.

### R3. Use `core/PID controller` (NOT manual Gain+Integrator+Sum)

```python
pid = mdl.create_component("core/PID controller", name="PID controller",
                           position=(x0 + 200, yc))
mdl.set_property_value(mdl.prop(pid, "controller_type"), "PI")
mdl.set_property_value(mdl.prop(pid, "kp"), "P")          # references model_init var
mdl.set_property_value(mdl.prop(pid, "ki"), "I")          # references model_init var
mdl.set_property_value(mdl.prop(pid, "int_init_value"), "I_initial")
mdl.set_property_value(mdl.prop(pid, "enb_output_limit_out"), "True")
mdl.set_property_value(mdl.prop(pid, "lower_sat_lim"), "0")
mdl.set_property_value(mdl.prop(pid, "upper_sat_lim"), "1")
```

Terminals: `in`, `out`, `reset` (when `show_reset = "level"`).

### R4. Parameters in `model_init`, NOT hardcoded properties

```python
init_code = (
    "# Numpy module is imported as 'np'\n"
    "# Scipy module is imported as 'sp'\n"
    "\n"
    "Ts = 100e-6\n"
    "P = 0.5e-3\n"
    "I = 0.1\n"
    "I_initial = 0.5\n"
)
mdl.set_model_init_code(init_code)
```

In components: `kp = "P"` (string referencing the variable, not the value).

### R5. Tag (Goto/From) for cross-domain signal routing

```python
goto_vout = mdl.create_tag(value="Vout", name="Goto_Vout",
    scope=const.TAG_SCOPE_LOCAL, kind=const.KIND_SP,
    direction=const.DIRECTION_IN, position=(x0, y0))

from_vout = mdl.create_tag(value="Vout", name="From_Vout",
    scope=const.TAG_SCOPE_LOCAL, kind=const.KIND_SP,
    direction=const.DIRECTION_OUT, position=(x0, yc))

mdl.create_connection(mdl.term(vout_meas, "out"), goto_vout)
mdl.create_connection(from_vout, mdl.term(sum1, "in1"))
```

### R6. Voltage Measurement → SP feedback via `sig_output`

```python
vout = mdl.create_component("core/Voltage Measurement", name="Vout", ...)
mdl.set_property_value(mdl.prop(vout, "sig_output"), "True")
mdl.set_property_value(mdl.prop(vout, "execution_rate"), "Ts")
# mdl.term(vout, "out") now usable as SP signal
```

### R7. Always include parasitics (RL, ESR)

```python
rl  = mdl.create_component("core/Resistor", name="RL",  ...)
esr = mdl.create_component("core/Resistor", name="ESR", ...)
mdl.set_property_value(mdl.prop(rl,  "resistance"), "0.1")
mdl.set_property_value(mdl.prop(esr, "resistance"), "0.001")
```

Wiring: `Vin → Iin → RL → L → converter.a_in`, `C_top → ESR → C → C_bot`.

---

## Verified Component Reference (2026.1 SP1)

### Power Electronics
| Component | Type | Terminals |
|-----------|------|-----------|
| Voltage Source | `core/Voltage Source` | p_node, n_node |
| Resistor | `core/Resistor` | p_node, n_node |
| Inductor | `core/Inductor` | p_node, n_node |
| Capacitor | `core/Capacitor` | p_node, n_node |
| Ground | `core/Ground` | node |
| Current Meas | `core/Current Measurement` | p_node, n_node |
| Voltage Meas | `core/Voltage Measurement` | p_node, n_node, out (when sig_output=True) |
| Contactor | `core/Single Pole Single Throw Contactor` | a_in, a_out |
| Boost | `core/Boost` | a_in, a_out, b_in, b_out, En, In |
| Buck | `core/Buck` | a_in, a_out, b_in, b_out, En, In |

### Signal Processing
| Component | Type | Terminals |
|-----------|------|-----------|
| Sum | `core/Sum` | in, in1, out |
| Gain | `core/Gain` | in, out |
| Constant | `core/Constant` | out |
| Comparator | `core/Comparator` | in, in1, out |
| Probe | `core/Probe` | in |
| Digital Probe | `core/Digital Probe` | in |
| SCADA Input | `core/SCADA Input` | out |
| PID controller | `core/PID controller` | in, out, reset |
| Logical operator | `core/Logical operator` | in, in1, out |

### Property notes
```python
mdl.set_property_value(mdl.prop(sum1, "signs"), "+-")        # in1=+, in2=−
mdl.set_property_value(mdl.prop(not_gate, "operator"), "NOT") # NOT, AND, OR
```

### Coordinate system
- Scene: 16384 × 16384, center (8192, 8192)
- Component spacing: ≥100 px
- `rotation`: `up` (default), `right` (90 CW), `down` (180), `left` (270)

---

## Workflow

### Step 0: Fetch latest SchematicAPI doc

```
WebFetch(url="https://www.typhoon-hil.com/documentation/typhoon-hil-api-documentation/schematic_api.html")
```

If fails → use built-in rules above.

### Step 1: Confirm spec

If user spec is incomplete, ask once for the missing fields. Fill defaults if user defers:
- Boost default: Vin=35V, Vout_ref=50V, fsw=10kHz, PI control
- Buck default: Vin=48V, Vout_ref=24V, fsw=20kHz, PI control
- 3φ inverter default: Vdc=700V, fsw=10kHz, FOC
- H-Bridge default: Vdc=400V, bipolar PWM

### Step 2: Generate script

Skeleton:
```python
# -*- coding: utf-8 -*-
"""<topology> closed-loop model generator."""
import os
from typhoon.api.schematic_editor import SchematicAPI
from typhoon.api.schematic_editor import const

mdl = SchematicAPI()
mdl.create_new_model()
x0, y0 = 8192, 8192

# 1. Power stage components
# 2. Power stage junctions
# 3. Power stage connections
# 4. Power stage properties
# 5. Control loop components
# 6. Control loop connections (Tags for cross-domain)
# 7. Control loop properties
# 8. model_init code block
# 9. Save and compile

mdl.save_as("<topology>_closed_loop.tse")
mdl.compile()
mdl.close_model()
```

### Step 3: Quality checklist (run before delivery)

- [ ] Pure ASCII (verified by decode check)
- [ ] Pre-built converter block used
- [ ] `core/PID controller` (no manual PI from primitives)
- [ ] All params in `model_init`
- [ ] Tag routing for cross-domain signals
- [ ] `sig_output="True"` on feedback Voltage Measurement
- [ ] RL and ESR parasitics present
- [ ] Ground present in power circuit
- [ ] `mdl.close_model()` at end

---

## Output

1. Save to `/mnt/user-data/outputs/<topology>/create_<topology>.py`
2. Brief the user:
   - Run inside Typhoon HIL Control Center: `python create_<topology>.py`
   - Parameters to tune live in `model_init` block
   - Generated `.tse` can be passed to `/tse-to-pytest` for test code

## Reference files (in repo)

- `references/topology_templates.md` — full code patterns per topology
- `references/boost_closed_loop_reference.tse` — verified working .tse for terminal-name lookup

When uncertain about a terminal name or property, read the reference `.tse` rather than guess.
