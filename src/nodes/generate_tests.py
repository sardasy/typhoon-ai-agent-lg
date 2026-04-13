"""
Node: generate_tests

Generates pytest test code from parsed TSE and test requirements.
Supports two modes: mock (synthetic waveforms) and typhoon (real HIL hardware).
"""

from __future__ import annotations

import logging
import textwrap
from typing import Any

from ..state import AgentState, make_event

logger = logging.getLogger(__name__)


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
    first_source_val = next(iter(sources.values()), 48.0) if sources else 48.0
    analog = parsed.get("analog_signals", [])
    out_signal = analog[0] if analog else "Vout"

    tests = []

    # Output voltage regulation test
    tests.append(textwrap.dedent(f"""\
        @pytest.mark.regulation
        @pytest.mark.parametrize("desired_reference", [
            {first_source_val * 0.8:.1f},
            {first_source_val:.1f},
            {first_source_val * 1.1:.1f},
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
            hil.set_source_value("{first_source}", nominal * vin_dist)
            hil.wait_msec(int(ts(200) * 1000))

            capture.start_capture(
                duration=0.5,
                signals=["{out_signal}"],
                rate=50000,
            )
            hil.wait_msec(int(0.6 * 1000))
            cap = capture.get_capture_results()
            samples = cap["{out_signal}"]

            mean_v = sum(samples) / len(samples)
            target = SOURCES["{first_source}"]  # expected output
            assert abs(mean_v - target) / target < VOLTAGE_TOLERANCE, \\
                f"Output {{mean_v:.3f}}V after disturbance (vin_dist={{vin_dist}})"

            # Restore
            hil.set_source_value("{first_source}", nominal)
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
            new_ref = {first_source_val * 1.1:.1f}
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
    """Generate a single mock test file with MockHilRunner."""
    topology = parsed.get("topology", "unknown")
    sources = parsed.get("sources", {})
    first_val = next(iter(sources.values()), 48.0) if sources else 48.0

    return textwrap.dedent(f"""\
        # Auto-generated mock test for {topology} topology
        # Runs without Typhoon HIL hardware using synthetic waveforms
        from __future__ import annotations

        import math
        import pytest


        class MockHilRunner:
            \"\"\"Generates synthetic waveform data for testing without hardware.\"\"\"

            def __init__(self, target_voltage: float = {first_val}):
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
                wn = 200.0  # natural frequency
                zeta = 0.7  # damping
                ripple_amp = self.target * 0.005  # 0.5% ripple
                for i in range(n):
                    t = i / rate
                    # Second-order step response
                    envelope = 1.0 - math.exp(-zeta * wn * t) * math.cos(
                        wn * math.sqrt(1 - zeta**2) * t
                    )
                    ripple = ripple_amp * math.sin(2 * math.pi * 100000 * t)
                    noise = random.gauss(0, self.target * 0.001)
                    samples.append(self.target * envelope + ripple + noise)
                return samples


        @pytest.fixture
        def hil():
            runner = MockHilRunner()
            runner.start()
            yield runner
            runner.stop()


        class TestOutputRegulation:
            @pytest.mark.parametrize("target", [{first_val * 0.8:.1f}, {first_val:.1f}, {first_val * 1.1:.1f}])
            def test_voltage_within_tolerance(self, hil, target):
                hil.target = target
                samples = hil.capture(duration=0.5, rate=50000)
                # Skip transient (first 20%)
                steady = samples[len(samples) // 5:]
                mean_v = sum(steady) / len(steady)
                assert abs(mean_v - target) / target < 0.05, \\
                    f"Output {{mean_v:.3f}}V vs target {{target}}V"

            def test_ripple_below_threshold(self, hil):
                samples = hil.capture(duration=0.1, rate=100000)
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

    if mode == "typhoon":
        files["constants.py"] = _gen_constants(parsed, reqs)
        files["conftest.py"] = _gen_conftest(parsed)
        files["pytest.ini"] = _gen_pytest_ini()
        files[f"test_{topology}.py"] = _gen_test_file(parsed, reqs)
        files["utils/__init__.py"] = ""
        files["utils/analysis.py"] = _gen_analysis_utils()
    else:
        files[f"test_mock_{topology}.py"] = _gen_mock_test(parsed, reqs)

    msg = f"Generated {len(files)} files ({mode} mode, topology={topology})"
    logger.info(msg)

    return {
        "generated_files": files,
        "events": [make_event("generate_tests", "action", msg, {
            "file_count": len(files),
            "mode": mode,
            "filenames": list(files.keys()),
        })],
    }
