"""
Node: generate_tests

Generates pytest test code from parsed TSE and test requirements.
Supports two modes: mock (synthetic waveforms) and typhoon (real HIL hardware).
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Any

from ..state import AgentState, make_event
from ..tools import get_hil_api_docs, get_pytest_api

logger = logging.getLogger(__name__)


def _load_codegen_config() -> dict[str, Any]:
    """Load configs/codegen.yaml (optional)."""
    cfg_path = Path(__file__).resolve().parents[2] / "configs" / "codegen.yaml"
    if not cfg_path.is_file():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    try:
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


# ---------------------------------------------------------------------------
# Typhoon mode: multi-file test suite
# ---------------------------------------------------------------------------

def _gen_constants(parsed: dict, reqs: list[dict]) -> str:
    """Generate constants.py with model path, signal names, and timing."""
    sources_repr = repr(parsed.get("sources", {}))
    scada_repr = repr(parsed.get("scada_inputs", {}))
    analog_repr = repr(parsed.get("analog_signals", []))
    digital_repr = repr(parsed.get("digital_signals", []))
    topo = parsed.get("topology", "unknown")
    ctrl_repr = repr(parsed.get("control_params", {}))
    sim_ts = parsed.get("sim_time_step", 1e-6)
    dsp_ts = parsed.get("dsp_timer_periods", 100e-6)

    return textwrap.dedent(f"""\
        # Auto-generated constants from TSE model
        # Topology: {topo}

        MODEL_PATH = "models/{parsed.get('model_name', 'model')}.tse"

        ANALOG_SIGNALS = {analog_repr}
        DIGITAL_SIGNALS = {digital_repr}

        SOURCES = {sources_repr}
        SCADA_INPUTS = {scada_repr}

        CONTROL_PARAMS = {ctrl_repr}

        SIM_TIME_STEP = {sim_ts}
        DSP_TIMER_PERIOD = {dsp_ts}

        # Tolerance defaults
        VOLTAGE_TOLERANCE = 0.05  # 5%
        RIPPLE_TOLERANCE = 0.02   # 2%
        SETTLING_TIME_S = 0.050   # 50ms


        def ts(n=1):
            \"\"\"Return n DSP timer periods in seconds.\"\"\"
            return DSP_TIMER_PERIOD * n
    """)


def _gen_conftest(parsed: dict) -> str:
    """Generate conftest.py with HIL setup/teardown fixtures."""
    return textwrap.dedent("""\
        # Auto-generated conftest for Typhoon HIL tests
        from __future__ import annotations

        import pytest
        import typhoon.api.hil as hil
        from typhoon.api.schematic_editor import model

        from constants import MODEL_PATH, SCADA_INPUTS, ts


        @pytest.fixture(scope="module")
        def hil_setup():
            \"\"\"Load, compile, and start the HIL model.\"\"\"
            model.load(MODEL_PATH)
            model.compile()
            hil.load_model(model.get_compiled_model_file())
            hil.start_simulation()
            hil.wait_msec(int(ts(50) * 1000))
            yield
            hil.stop_simulation()


        @pytest.fixture(autouse=True)
        def reset_parameters(hil_setup):
            \"\"\"Reset SCADA inputs to default values before each test.\"\"\"
            for name, value in SCADA_INPUTS.items():
                hil.set_scada_input_value(name, value)
            hil.wait_msec(int(ts(10) * 1000))
    """)


def _gen_pytest_ini() -> str:
    """Generate pytest.ini with markers and settings."""
    return textwrap.dedent("""\
        [pytest]
        markers =
            regulation: Output regulation tests
            ripple: Ripple measurement tests
            settling: Settling time tests
            rms: RMS voltage tests
            phase_shift: Phase shift control tests
            disturbance: Input disturbance response tests
            extreme: Extreme condition tests
        timeout = 120
    """)


def _gen_analysis_utils() -> str:
    """Generate utils/analysis.py with waveform analysis helpers."""
    return textwrap.dedent("""\
        # Auto-generated waveform analysis utilities
        from __future__ import annotations

        import math


        def calc_ripple(samples: list[float]) -> float:
            \"\"\"Calculate peak-to-peak ripple as fraction of mean.\"\"\"
            if not samples:
                return 0.0
            mean_val = sum(samples) / len(samples)
            if abs(mean_val) < 1e-9:
                return 0.0
            pk_pk = max(samples) - min(samples)
            return pk_pk / abs(mean_val)


        def calc_rms(samples: list[float]) -> float:
            \"\"\"Calculate RMS value of samples.\"\"\"
            if not samples:
                return 0.0
            return math.sqrt(sum(x * x for x in samples) / len(samples))


        def calc_settling_time(
            samples: list[float],
            target: float,
            tolerance: float,
            dt: float,
        ) -> float:
            \"\"\"Find time when signal enters and stays within tolerance band.\"\"\"
            band_lo = target * (1 - tolerance)
            band_hi = target * (1 + tolerance)
            last_outside = -1
            for i, v in enumerate(samples):
                if v < band_lo or v > band_hi:
                    last_outside = i
            if last_outside < 0:
                return 0.0
            return (last_outside + 1) * dt


        def calc_thd(samples: list[float], fundamental_freq: float, dt: float) -> float:
            \"\"\"Estimate Total Harmonic Distortion (simplified).\"\"\"
            # Placeholder: real implementation would use FFT
            return 0.0


        def calc_efficiency(p_in_samples: list[float], p_out_samples: list[float]) -> float:
            \"\"\"Calculate average efficiency from power samples.\"\"\"
            if not p_in_samples or not p_out_samples:
                return 0.0
            avg_in = sum(p_in_samples) / len(p_in_samples)
            avg_out = sum(p_out_samples) / len(p_out_samples)
            if abs(avg_in) < 1e-9:
                return 0.0
            return avg_out / avg_in
    """)


def _gen_test_file(parsed: dict, reqs: list[dict]) -> str:
    """Generate test_{topology}.py with parametrized test functions."""
    topology = parsed.get("topology", "unknown")
    sources = parsed.get("sources", {})
    first_source = next(iter(sources.keys()), "Vin") if sources else "Vin"
    analog = parsed.get("analog_signals", [])
    out_signal = analog[0] if analog else "Vout"

    # Output reference = SCADA input named "Reference" when present, otherwise
    # fall back to the first analog source value. This is the *setpoint* the
    # controller regulates toward, NOT the input voltage.
    scada = parsed.get("scada_inputs", {})
    ref_nominal = (
        float(scada.get("Reference"))
        if "Reference" in scada
        else float(next(iter(sources.values()), 48.0))
    )

    tests = []

    # Output voltage regulation test
    tests.append(textwrap.dedent(f"""\
        @pytest.mark.regulation
        @pytest.mark.parametrize("desired_reference", [
            {ref_nominal * 0.8:.1f},
            {ref_nominal:.1f},
            {ref_nominal * 1.1:.1f},
        ])
        def test_output_voltage_regulation(hil_setup, desired_reference):
            \"\"\"Verify output tracks reference within tolerance.\"\"\"
            hil.set_scada_input_value("Reference", desired_reference)
            hil.wait_msec(int(ts(100) * 1000))

            capture.start_capture(
                duration=0.5,
                signals=["{out_signal}"],
                rate=50000,
                trigger_type="Analog",
                trigger_signal="{out_signal}",
            )
            hil.wait_msec(int(0.6 * 1000))
            cap = capture.get_capture_results()
            samples = cap["{out_signal}"]

            mean_v = sum(samples) / len(samples)
            assert abs(mean_v - desired_reference) / desired_reference < VOLTAGE_TOLERANCE, \\
                f"Output {{mean_v:.3f}}V deviates from ref {{desired_reference}}V"
    """))

    # Disturbance response test
    tests.append(textwrap.dedent(f"""\
        @pytest.mark.disturbance
        @pytest.mark.parametrize("vin_dist", [0.80, 1.10])
        def test_input_disturbance_response(hil_setup, vin_dist):
            \"\"\"Verify output recovers after input voltage disturbance.\"\"\"
            nominal = SOURCES["{first_source}"]
            hil.set_source_constant_value("{first_source}", nominal * vin_dist)
            hil.wait_msec(int(ts(200) * 1000))

            capture.start_capture(
                duration=0.5,
                signals=["{out_signal}"],
                rate=50000,
            )
            hil.wait_msec(int(0.6 * 1000))
            cap = capture.get_capture_results()
            samples = cap["{out_signal}"]

            # Regulated output should recover to the reference setpoint
            mean_v = sum(samples) / len(samples)
            target = {ref_nominal:.1f}  # regulated output reference
            assert abs(mean_v - target) / target < VOLTAGE_TOLERANCE, \\
                f"Output {{mean_v:.3f}}V after disturbance (vin_dist={{vin_dist}})"

            # Restore
            hil.set_source_constant_value("{first_source}", nominal)
    """))

    # Topology-specific tests
    for req in reqs:
        metric = req.get("metric", "")
        if metric == "ripple":
            tests.append(textwrap.dedent(f"""\
        @pytest.mark.ripple
        def test_output_ripple(hil_setup):
            \"\"\"Verify output ripple is below threshold.\"\"\"
            from utils.analysis import calc_ripple
            hil.wait_msec(int(ts(100) * 1000))
            capture.start_capture(
                duration=0.1,
                signals=["{out_signal}"],
                rate=100000,
            )
            hil.wait_msec(int(0.15 * 1000))
            cap = capture.get_capture_results()
            ripple = calc_ripple(cap["{out_signal}"])
            assert ripple < RIPPLE_TOLERANCE, f"Ripple {{ripple:.4f}} exceeds {{RIPPLE_TOLERANCE}}"
            """))

        elif metric == "settling_time":
            tests.append(textwrap.dedent(f"""\
        @pytest.mark.settling
        def test_settling_time(hil_setup):
            \"\"\"Verify settling time after step reference change.\"\"\"
            from utils.analysis import calc_settling_time
            new_ref = {ref_nominal * 1.1:.1f}
            hil.set_scada_input_value("Reference", new_ref)
            capture.start_capture(
                duration=0.5,
                signals=["{out_signal}"],
                rate=50000,
            )
            hil.wait_msec(int(0.6 * 1000))
            cap = capture.get_capture_results()
            t_settle = calc_settling_time(cap["{out_signal}"], new_ref, 0.02, 1/50000)
            assert t_settle < SETTLING_TIME_S, f"Settling {{t_settle:.4f}}s exceeds {{SETTLING_TIME_S}}s"
            """))

        elif metric == "rms_voltage":
            tests.append(textwrap.dedent(f"""\
        @pytest.mark.rms
        def test_rms_voltage(hil_setup):
            \"\"\"Verify RMS voltage at output.\"\"\"
            from utils.analysis import calc_rms
            hil.wait_msec(int(ts(100) * 1000))
            capture.start_capture(
                duration=0.2,
                signals=["{out_signal}"],
                rate=50000,
            )
            hil.wait_msec(int(0.25 * 1000))
            cap = capture.get_capture_results()
            rms = calc_rms(cap["{out_signal}"])
            target_rms = {req.get('target_value', 230.0)}
            assert abs(rms - target_rms) / target_rms < 0.05, f"RMS {{rms:.1f}}V vs target {{target_rms}}V"
            """))

    test_body = "\n\n".join(tests)

    return textwrap.dedent(f"""\
        # Auto-generated tests for {topology} topology
        from __future__ import annotations

        import pytest
        import typhoon.api.hil as hil
        from typhoon.test import capture

        from constants import (
            SOURCES, SCADA_INPUTS, VOLTAGE_TOLERANCE, RIPPLE_TOLERANCE,
            SETTLING_TIME_S, ts,
        )


    """) + test_body


def _gen_mock_test(parsed: dict, reqs: list[dict]) -> str:
    """Generate a single mock test file with MockHilRunner.

    The fixture is named ``mock_hil`` - intentionally different from the real
    typhoon ``hil`` module so validate_code's hil.* API check doesn't
    false-positive on method calls like ``mock_hil.capture(...)``.
    """
    topology = parsed.get("topology", "unknown")
    sources = parsed.get("sources", {})
    scada = parsed.get("scada_inputs", {})
    # Setpoint the controller regulates toward. Prefer SCADA "Reference",
    # otherwise the first analog source value.
    ref_val = (
        float(scada.get("Reference"))
        if "Reference" in scada
        else float(next(iter(sources.values()), 48.0))
    )

    return textwrap.dedent(f"""\
        # Auto-generated mock test for {topology} topology
        # Runs without Typhoon HIL hardware using synthetic waveforms.
        # Settling: second-order system with wn=2000 rad/s, zeta=0.7
        #   -> 4*tau = 2.86 ms. A 20% skip of a 100 ms capture (=20 ms)
        #   safely drops the transient for steady-state measurements.
        from __future__ import annotations

        import math
        import pytest


        class MockHilRunner:
            \"\"\"Generates synthetic waveform data for testing without hardware.\"\"\"

            def __init__(self, target_voltage: float = {ref_val}):
                self.target = target_voltage
                self.running = False

            def start(self):
                self.running = True

            def stop(self):
                self.running = False

            def capture(self, duration: float, rate: int) -> list[float]:
                \"\"\"Generate second-order step response + ripple + noise.\"\"\"
                import random
                n = int(duration * rate)
                samples = []
                wn = 2000.0          # natural frequency, rad/s (settling ~3 ms)
                zeta = 0.7           # damping
                ripple_amp = self.target * 0.005   # 0.5% ripple at 100 kHz
                wd = wn * math.sqrt(1 - zeta * zeta)
                for i in range(n):
                    t = i / rate
                    # Second-order step response: 1 - e^(-zwn t) * cos(wd t)
                    envelope = 1.0 - math.exp(-zeta * wn * t) * math.cos(wd * t)
                    ripple = ripple_amp * math.sin(2 * math.pi * 100000 * t)
                    noise = random.gauss(0, self.target * 0.001)
                    samples.append(self.target * envelope + ripple + noise)
                return samples


        @pytest.fixture
        def mock_hil():
            runner = MockHilRunner()
            runner.start()
            yield runner
            runner.stop()


        class TestOutputRegulation:
            @pytest.mark.parametrize("target", [{ref_val * 0.8:.1f}, {ref_val:.1f}, {ref_val * 1.1:.1f}])
            def test_voltage_within_tolerance(self, mock_hil, target):
                mock_hil.target = target
                samples = mock_hil.capture(duration=0.5, rate=50000)
                # Skip transient (first 20% = 100 ms at 500 ms total)
                steady = samples[len(samples) // 5:]
                mean_v = sum(steady) / len(steady)
                assert abs(mean_v - target) / target < 0.05, \\
                    f"Output {{mean_v:.3f}}V vs target {{target}}V"

            def test_ripple_below_threshold(self, mock_hil):
                samples = mock_hil.capture(duration=0.1, rate=100000)
                # Skip transient (first 20% = 20 ms; 4*tau settling ~3 ms)
                steady = samples[len(samples) // 5:]
                mean_v = sum(steady) / len(steady)
                pk_pk = max(steady) - min(steady)
                ripple = pk_pk / abs(mean_v) if abs(mean_v) > 1e-9 else 0
                assert ripple < 0.02, f"Ripple {{ripple:.4f}} exceeds 2%"
    """)


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

async def generate_tests(state: AgentState) -> dict[str, Any]:
    """Generate pytest test files from parsed TSE and requirements."""
    parsed = state.get("parsed_tse")
    reqs = state.get("test_requirements", [])
    mode = state.get("codegen_mode", "mock")

    if not parsed:
        return {
            "error": "No parsed TSE data for code generation",
            "events": [make_event("generate_tests", "error", "No parsed TSE data")],
        }

    topology = parsed.get("topology", "unknown")
    files: dict[str, str] = {}

    # Load HIL API docs (hil_api.html) and pytest introspection index.
    # Both failures are non-fatal: we still emit tests without their headers.
    codegen_cfg = _load_codegen_config()
    doc_cfg = codegen_cfg.get("hil_api_docs", {}) or {}
    pyt_cfg = codegen_cfg.get("pytest_api", {}) or {}

    api_docs = get_hil_api_docs(doc_cfg.get("path"))
    api_count = api_docs.count() if api_docs.is_loaded() else 0
    api_header = ""
    if api_docs.is_loaded():
        max_items = int(doc_cfg.get("context_header_max_items", 30))
        api_header = api_docs.summary_for_context(max_items=max_items) + "\n"

    pytest_api = get_pytest_api()
    pytest_symbols = len(pytest_api.list_public_api()) if pytest_api.is_loaded() else 0
    pytest_header = ""
    if pytest_api.is_loaded():
        pytest_max = int(pyt_cfg.get("context_header_max_items", 20))
        pytest_header = pytest_api.summary_for_context(max_items=pytest_max) + "\n"

    combined_header = api_header + pytest_header

    if mode == "typhoon":
        files["constants.py"] = _gen_constants(parsed, reqs)
        files["conftest.py"] = _gen_conftest(parsed)
        files["pytest.ini"] = _gen_pytest_ini()
        files[f"test_{topology}.py"] = combined_header + _gen_test_file(parsed, reqs)
        files["utils/__init__.py"] = ""
        files["utils/analysis.py"] = _gen_analysis_utils()
    else:
        files[f"test_mock_{topology}.py"] = combined_header + _gen_mock_test(parsed, reqs)

    msg = f"Generated {len(files)} files ({mode} mode, topology={topology})"
    if api_count:
        msg += f"; {api_count} HIL API members loaded"
    if pytest_symbols:
        msg += f"; pytest v{pytest_api.version()} introspected ({pytest_symbols} symbols)"
    logger.info(msg)

    return {
        "generated_files": files,
        "events": [make_event("generate_tests", "action", msg, {
            "file_count": len(files),
            "mode": mode,
            "filenames": list(files.keys()),
            "hil_api_members": api_count,
            "hil_api_doc_path": api_docs._loaded_path or "",
            "pytest_version": pytest_api.version() if pytest_api.is_loaded() else "",
            "pytest_public_symbols": pytest_symbols,
        })],
    }
