"""
Model Settings preset loader.

Reads `configs/model_presets.yaml` and exposes lookup helpers. Consumed by
`nodes/load_model.py` when `model.preset: "<name>"` is set in model.yaml.
Missing or malformed preset files degrade to an empty catalog (log + continue)
so mock-mode tests do not fail on environments without the YAML.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_PATH = "configs/model_presets.yaml"


@lru_cache(maxsize=4)
def load_presets(path: str = DEFAULT_PATH) -> dict[str, dict[str, Any]]:
    """Load the preset catalog from YAML. Returns {} on missing/invalid file."""
    p = Path(path)
    if not p.exists():
        logger.info(f"Preset file not found: {path}")
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        logger.error(f"Preset YAML parse error: {e}")
        return {}
    presets = raw.get("presets", {})
    if not isinstance(presets, dict):
        logger.error("Preset file root.presets must be a mapping")
        return {}
    return presets


def get_preset(name: str, path: str = DEFAULT_PATH) -> dict[str, Any] | None:
    """Look up a preset by name. None if missing."""
    return load_presets(path).get(name)


def list_presets(path: str = DEFAULT_PATH) -> list[str]:
    """Return the sorted list of preset names."""
    return sorted(load_presets(path).keys())
