# Downstream pytest project template

This directory contains the **pytest project skeleton** for downstream
test suites generated FROM THAA (or hand-written by operators against a
specific HIL model). It implements the Mirim Syscon dual-path
DUTInterface pattern — same test code runs on **VHIL** and **real ECU
via XCP**.

## What's here

- ``conftest.py`` — DUTInterface ABC + HILSimDUT (VHIL) + XCPDUT
  (real ECU) + the standard fixtures (``dut``, ``model_path``,
  ``setup``, ``reset_parameters``).

## Usage

Copy ``conftest.py`` to the root of a new pytest project:

```
my_project/
├── conftest.py        <-- this file
├── tests/
│   ├── unit/          (VHIL only, fast)
│   ├── integration/   (VHIL + XCP)
│   └── hw/            (XCP only, real ECU)
└── models/
    └── boost.tse
```

Then write tests using only ``DUTInterface`` methods so the same code
runs in both modes:

```python
def test_steady_state(dut, setup, reset_parameters):
    dut.start_simulation()
    dut.wait_sec(0.1)
    v = dut.read_signal("Vout")
    assert 4.95 <= v <= 5.05
    dut.stop_simulation()
```

Switch paths via env var:

```bash
DUT_MODE=vhil pytest tests/unit/      # VHIL simulator
DUT_MODE=xcp pytest tests/integration/ # real ECU
```

## Hardware-only tests

Mark tests that need the physical DUT with ``@pytest.mark.hw_required``
— they auto-skip when ``DUT_MODE != xcp``.

## Reference: Mirim Syscon CLAUDE.md

This template implements:
- Hard Rule 3.1 (ASCII-only Python source)
- Hard Rule 3.2 (``pd.Timedelta`` indexing — see also
  ``src/timedelta_helpers.py`` in the agent repo)
- Hard Rule 3.3 (``model_path`` fixture from ``pytestconfig.rootpath``)
- Hard Rule 3.5 (``typhoon.test.*`` high-level API only)
