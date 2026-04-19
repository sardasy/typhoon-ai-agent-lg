"""
pytest fixtures for VSM inverter GFM compliance tests.

Manages the HIL connection lifecycle (load_model -> start -> stop) and
exposes shared helpers (test parameters, capture utilities, source resets).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

# Make sibling packages (utils, models) importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils import signal_analysis as sa  # noqa: E402

CONFIG_PATH = ROOT / "config" / "test_params.json"


def _load_cfg() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as fp:
        return json.load(fp)


@pytest.fixture(scope="session")
def cfg() -> dict:
    """Loaded test_params.json — IEEE 2800 thresholds + signal lists."""
    return _load_cfg()


@pytest.fixture(scope="session")
def hil_connection(cfg):
    """Session-level HIL connection. Loads .cpd, starts simulation.

    Skips the entire suite when typhoon.api.hil is unavailable so the same
    file can be collected on machines without Typhoon HIL Control Center.
    """
    try:
        import typhoon.api.hil as hil
    except ImportError:
        pytest.skip("typhoon.api.hil not available — skipping HIL tests")

    cpd = cfg["model"]["cpd_path"]
    if not os.path.isfile(cpd):
        # Try compiling on-the-fly
        from models.model_builder import compile_existing
        cpd = compile_existing()

    use_vhil = os.environ.get("THAA_USE_VHIL", "1") == "1"
    hil.load_model(file=cpd, offlineMode=False, vhil_device=use_vhil)
    hil.start_simulation()
    time.sleep(cfg["test_execution"]["settling_time_s"])

    yield hil

    hil.stop_simulation()


@pytest.fixture(scope="function")
def reset_sources(hil_connection, cfg):
    """Restore Pref / Qref / VSM params and grid source between tests."""
    hil = hil_connection
    grid = cfg["grid"]
    vsm = cfg["vsm"]

    hil.set_source_sine_waveform(
        cfg["signals"]["grid_source"],
        rms=grid["nominal_voltage_rms_ll"],
        frequency=grid["nominal_frequency_hz"],
        phase=0.0,
    )
    hil.set_scada_input_value("Pref", 0.0)
    hil.set_scada_input_value("Qref", 0.0)
    hil.set_scada_input_value("J", vsm["moment_of_inertia_J_default"])
    hil.set_scada_input_value("D", vsm["damping_D_default"])
    hil.set_scada_input_value("Kv", vsm["voltage_droop_Kv_default"])
    time.sleep(cfg["test_execution"]["settling_time_s"])
    yield


@pytest.fixture
def capture_helper(cfg):
    """Wrapper around typhoon.test.capture for typed steady-state captures."""
    from typhoon.test.capture import start_capture, get_capture_results

    class CaptureHelper:
        def __init__(self):
            self.rate = cfg["test_execution"]["capture_rate_hz"]

        def capture(self, signals, duration_s=None, trigger_source=None,
                    trigger_threshold=0.0, trigger_edge="Rising edge"):
            d = duration_s or cfg["test_execution"]["default_capture_duration_s"]
            kwargs = dict(
                duration=d,
                signals=list(signals),
                rate=self.rate,
            )
            if trigger_source:
                kwargs.update(
                    trigger_source=trigger_source,
                    trigger_threshold=trigger_threshold,
                    trigger_edge=trigger_edge,
                )
            start_capture(**kwargs)
            time.sleep(d + 0.5)
            return get_capture_results()

    return CaptureHelper()


@pytest.fixture
def analysis():
    """Expose signal_analysis as a fixture for cleaner test bodies."""
    return sa
