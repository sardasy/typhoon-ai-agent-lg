# -*- coding: utf-8 -*-
"""
Project-level conftest.py - dual-path DUT abstraction.

This file defines:
  - DUTInterface:     abstract base class for any device-under-test
  - HILSimDUT:        VHIL implementation (uses typhoon.api.hil)
  - XCPDUT:           real-ECU implementation (uses pyXCP / pyA2L)
  - dut fixture:      selects implementation via DUT_MODE env var
  - setup fixture:    loads/compiles model and binds it to VHIL device
  - reset_parameters: restores SCADA / source defaults before each test

CRITICAL CONVENTIONS (see CLAUDE.md):
  - This file is pure ASCII. All comments in English.
  - Use typhoon.test.* high-level APIs only (capture, signals, ranges, reporting).
  - Never call hil.connect() / hil.disconnect() / time.sleep().
  - Capture DataFrame index is timedelta64[ns]; manual slicing must use
    pd.Timedelta or the ts() helper in constants.py.
  - DUT_MODE selects the path:
        DUT_MODE=vhil  -> HILSimDUT  (default)
        DUT_MODE=xcp   -> XCPDUT     (requires real ECU + A2L file)

Hardware-only scenarios should use @pytest.mark.hw_required and will be
skipped automatically when DUT_MODE != xcp.
"""

import os
from abc import ABC, abstractmethod
from pathlib import Path

import pytest

from typhoon.api import hil
from typhoon.api.schematic_editor import model
import typhoon.test.reporting.messages as report


# ---------------------------------------------------------------------------
# Constants (override in project-level constants.py if you have one)
# ---------------------------------------------------------------------------

DUT_MODE_ENV = "DUT_MODE"
DUT_MODE_VHIL = "vhil"
DUT_MODE_XCP = "xcp"
DUT_MODE_DEFAULT = DUT_MODE_VHIL


# ---------------------------------------------------------------------------
# DUT Interface
# ---------------------------------------------------------------------------

class DUTInterface(ABC):
    """
    Abstract base class for the device under test.

    Tests should use only the methods defined here. Each concrete
    implementation maps these calls to either VHIL simulation primitives
    (HILSimDUT) or real ECU access via XCP (XCPDUT).
    """

    # ---- lifecycle ----
    @abstractmethod
    def start_simulation(self) -> None:
        ...

    @abstractmethod
    def stop_simulation(self) -> None:
        ...

    @abstractmethod
    def wait_sec(self, seconds: float) -> None:
        ...

    @abstractmethod
    def is_hardware(self) -> bool:
        """True for real-ECU implementations, False for VHIL."""
        ...

    # ---- I/O ----
    @abstractmethod
    def set_source(self, name: str, value: float) -> None:
        ...

    @abstractmethod
    def set_scada_input(self, name: str, value: float) -> None:
        ...

    @abstractmethod
    def read_signal(self, name: str) -> float:
        ...

    # ---- fault injection (extended by fault-injector subagent) ----
    def inject_overvoltage(self, level_v: float, ramp_time_s: float) -> None:
        raise NotImplementedError("Implement in subclass or call fault-injector subagent")

    def inject_undervoltage(self, level_v: float, ramp_time_s: float) -> None:
        raise NotImplementedError

    def inject_overcurrent(self, target_a: float, ramp_time_s: float) -> None:
        raise NotImplementedError

    def inject_source_loss(self) -> None:
        raise NotImplementedError

    def inject_sensor_fault(self, signal: str, mode: str) -> None:
        raise NotImplementedError

    def expect_trip(self, within_ms: float) -> bool:
        raise NotImplementedError

    def is_tripped(self) -> bool:
        raise NotImplementedError

    def clear_fault(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# VHIL implementation
# ---------------------------------------------------------------------------

class HILSimDUT(DUTInterface):
    """VHIL-backed DUT. Uses typhoon.api.hil directly."""

    def __init__(self, fault_flag_signal: str = "fault_flag"):
        self._fault_flag_signal = fault_flag_signal

    # lifecycle
    def start_simulation(self) -> None:
        hil.start_simulation()

    def stop_simulation(self) -> None:
        hil.stop_simulation()

    def wait_sec(self, seconds: float) -> None:
        hil.wait_sec(seconds)

    def is_hardware(self) -> bool:
        return False

    # I/O
    def set_source(self, name: str, value: float) -> None:
        hil.set_source_constant_value(name, value=value)

    def set_scada_input(self, name: str, value: float) -> None:
        hil.set_scada_input_value(name, value)

    def read_signal(self, name: str) -> float:
        return float(hil.read_analog_signal(name=name))

    # fault flag helpers (used by expect_trip)
    def is_tripped(self) -> bool:
        try:
            return self.read_signal(self._fault_flag_signal) > 0.5
        except Exception:
            return False

    def expect_trip(self, within_ms: float) -> bool:
        """Poll fault_flag at 1ms cadence until trip or timeout."""
        elapsed = 0.0
        step = 0.001
        while elapsed * 1000.0 < within_ms:
            if self.is_tripped():
                return True
            self.wait_sec(step)
            elapsed += step
        return False


# ---------------------------------------------------------------------------
# XCP / real-ECU implementation
# ---------------------------------------------------------------------------

class XCPDUT(DUTInterface):
    """
    Real-ECU DUT via XCP protocol.

    Lazy-imports pyxcp + pya2ltool so VHIL-only sessions don't require the
    XCP stack to be installed. Configure via env vars or the ECU_CONFIG
    fixture (TODO: define in project-level conftest extension).
    """

    def __init__(self, a2l_path: str, transport_url: str,
                 seed_key_dll: str = None,
                 fault_flag_signal: str = "fault_flag"):
        from pya2ltool.parser import parse_a2l_file  # type: ignore
        from pyxcp.master import Master              # type: ignore

        self._a2l = parse_a2l_file(a2l_path)
        self._master = Master.from_url(transport_url)
        if seed_key_dll:
            self._master.set_seed_key_dll(seed_key_dll)
        self._master.connect()
        self._fault_flag_signal = fault_flag_signal

    # lifecycle
    def start_simulation(self) -> None:
        # Real ECU is always running; this is a no-op or a "release run/stop" command.
        pass

    def stop_simulation(self) -> None:
        pass

    def wait_sec(self, seconds: float) -> None:
        # Real ECU runs in wall-clock. time.sleep is acceptable here ONLY
        # because there is no simulated clock to drift against. VHIL path
        # must continue to use hil.wait_sec.
        import time
        time.sleep(seconds)

    def is_hardware(self) -> bool:
        return True

    # I/O - via XCP DAQ + STIM
    def set_source(self, name: str, value: float) -> None:
        # Maps "source name" to a calibration / measurement variable in A2L.
        addr = self._a2l.measurement_address(name)
        self._master.short_upload(addr, length=4)  # placeholder
        # Real implementation: master.download(addr, encoded_value)
        raise NotImplementedError("XCP source override requires per-project mapping")

    def set_scada_input(self, name: str, value: float) -> None:
        # Most ECUs do not expose SCADA-style inputs; map to a calibration var.
        raise NotImplementedError("XCP SCADA-equivalent requires per-project mapping")

    def read_signal(self, name: str) -> float:
        addr = self._a2l.measurement_address(name)
        raw = self._master.short_upload(addr, length=4)
        # Apply A2L computation method (linear / formula / table)
        return self._a2l.convert_raw(name, raw)

    def is_tripped(self) -> bool:
        try:
            return self.read_signal(self._fault_flag_signal) > 0.5
        except Exception:
            return False

    def expect_trip(self, within_ms: float) -> bool:
        import time
        deadline = time.monotonic() + within_ms / 1000.0
        while time.monotonic() < deadline:
            if self.is_tripped():
                return True
            time.sleep(0.001)
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _resolve_dut_mode() -> str:
    return os.environ.get(DUT_MODE_ENV, DUT_MODE_DEFAULT).lower()


def pytest_collection_modifyitems(config, items):
    """Skip hw_required tests automatically when not in XCP mode."""
    mode = _resolve_dut_mode()
    if mode == DUT_MODE_XCP:
        return
    skip_hw = pytest.mark.skip(reason="hw_required: needs DUT_MODE=xcp")
    for item in items:
        if "hw_required" in item.keywords:
            item.add_marker(skip_hw)


@pytest.fixture(scope="session")
def dut_mode():
    return _resolve_dut_mode()


@pytest.fixture(scope="session")
def model_path(pytestconfig):
    """
    Resolve MODEL_PATH from project root. Override per-test by parametrizing
    or by setting MODEL_PATH env var.
    """
    env_path = os.environ.get("MODEL_PATH")
    if env_path:
        return Path(env_path)
    # Default convention: <root>/models/<topology>/<topology>.tse
    # Override in project-level conftest if your layout differs.
    default = pytestconfig.rootpath / "models" / "default" / "default.tse"
    return default


@pytest.fixture(scope="module")
def setup(model_path, dut_mode):
    """
    Module-scoped: load + compile + bind to VHIL.

    For XCP mode, this fixture only emits a report message; the real ECU
    is assumed to already be running its production firmware.
    """
    if dut_mode == DUT_MODE_VHIL:
        report.report_message("VHIL mode: loading model {}".format(model_path))
        model.load(str(model_path))
        model.compile(conditional_compile=True)
        compiled = model.get_compiled_model_file(str(model_path))
        hil.load_model(compiled, vhil_device=True)
    elif dut_mode == DUT_MODE_XCP:
        report.report_message("XCP mode: skipping model load (real ECU)")
    else:
        raise RuntimeError("Unknown DUT_MODE: {}".format(dut_mode))


@pytest.fixture()
def dut(dut_mode):
    """Return the DUT implementation matching DUT_MODE."""
    if dut_mode == DUT_MODE_VHIL:
        return HILSimDUT()
    elif dut_mode == DUT_MODE_XCP:
        a2l = os.environ.get("XCP_A2L_PATH")
        url = os.environ.get("XCP_TRANSPORT_URL")
        seed_dll = os.environ.get("XCP_SEED_KEY_DLL")
        if not a2l or not url:
            pytest.skip("XCP mode requires XCP_A2L_PATH and XCP_TRANSPORT_URL")
        return XCPDUT(a2l_path=a2l, transport_url=url, seed_key_dll=seed_dll)
    else:
        raise RuntimeError("Unknown DUT_MODE: {}".format(dut_mode))


@pytest.fixture()
def reset_parameters(dut, dut_mode):
    """
    Restore SCADA / source defaults before each test.

    Override in project-level conftest with your actual source/SCADA names.
    Default no-op to keep this skeleton runnable.
    """
    if dut_mode == DUT_MODE_VHIL:
        # Example - replace with your real defaults:
        # dut.set_scada_input("PWM Enable", 1.0)
        # dut.set_scada_input("Reference", 50.0)
        # dut.set_source("Vin", 35.0)
        pass
    yield
