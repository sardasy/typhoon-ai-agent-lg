"""
Model loader for the existing VSM inverter .tse model (invertertest.tse).

This project does NOT rebuild the schematic from scratch — the user has provided
a pre-built virtual sync machine inverter.tse model. We only compile it to
.cpd via the SchematicAPI and resolve the path used by the runtime.

If you want to programmatically construct the model with SchematicAPI instead,
replace ``compile_existing()`` with a ``build_from_scratch()`` routine.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "test_params.json"


def _load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as fp:
        return json.load(fp)


def compile_existing(tse_path: str | None = None) -> str:
    """Compile the existing .tse model and return the .cpd path.

    Returns the path to the generated .cpd file (loaded by hil.load_model()).
    """
    cfg = _load_config()
    tse = tse_path or cfg["model"]["tse_path"]

    if not os.path.isfile(tse):
        raise FileNotFoundError(f".tse model not found: {tse}")

    try:
        from typhoon.api.schematic_editor import SchematicAPI
    except ImportError as exc:
        raise RuntimeError(
            "typhoon.api.schematic_editor is not available. Install Typhoon HIL "
            "Control Center and ensure the bundled Python is on PATH."
        ) from exc

    mdl = SchematicAPI()
    mdl.load(tse)
    logger.info("Loaded schematic: %s", tse)

    if not mdl.compile():
        raise RuntimeError(f"SchematicAPI.compile() failed for {tse}")

    cpd = cfg["model"]["cpd_path"]
    if not os.path.isfile(cpd):
        raise RuntimeError(
            f"Compilation reported success but .cpd missing at {cpd}. "
            "Check 'cpd_path' in config/test_params.json."
        )
    logger.info("Compiled .cpd: %s", cpd)
    return cpd


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(compile_existing())
