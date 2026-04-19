"""
Standalone runner — applies VSM stimulus and captures waveforms without
pytest. Useful for ad-hoc bring-up before running the full IEEE 2800 suite.

Usage:
    python tests/test_runner.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vsm_runner")


def _load_cfg() -> dict:
    with open(ROOT / "config" / "test_params.json", encoding="utf-8") as fp:
        return json.load(fp)


def main() -> int:
    try:
        import typhoon.api.hil as hil
        from typhoon.test.capture import start_capture, get_capture_results
    except ImportError:
        log.error("typhoon.api.hil not available — install Typhoon HIL Control Center.")
        return 1

    cfg = _load_cfg()
    cpd = cfg["model"]["cpd_path"]

    log.info("Loading model: %s", cpd)
    hil.load_model(file=cpd, offlineMode=False, vhil_device=True)
    hil.start_simulation()

    try:
        # 1. Steady-state at nominal Pref = 5 kW, Qref = 0
        hil.set_source_sine_waveform(
            cfg["signals"]["grid_source"],
            rms=cfg["grid"]["nominal_voltage_rms_ll"],
            frequency=cfg["grid"]["nominal_frequency_hz"],
            phase=0.0,
        )
        hil.set_scada_input_value("J", 0.3)
        hil.set_scada_input_value("D", 10.0)
        hil.set_scada_input_value("Pref", 5000.0)
        hil.set_scada_input_value("Qref", 0.0)
        time.sleep(2.0)

        # 2. Capture key signals
        signals = ["Va", "Vb", "Vc", "Ia", "Ib", "Ic", "Pe", "Qe", "w", "teta"]
        log.info("Capturing 1.0s @ 50 kHz: %s", signals)
        start_capture(duration=1.0, signals=signals, rate=50000)
        time.sleep(1.5)

        result = get_capture_results()
        log.info("Capture keys: %s", list(result.keys()))
        log.info("Done.")
        return 0
    finally:
        hil.stop_simulation()


if __name__ == "__main__":
    sys.exit(main())
