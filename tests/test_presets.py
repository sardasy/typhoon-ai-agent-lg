"""
Tests for the Model Settings preset DB and its load_model integration.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from src.nodes.load_model import load_model
from src.presets import get_preset, list_presets, load_presets


REPO_ROOT = Path(__file__).resolve().parent.parent
PRESETS_PATH = str(REPO_ROOT / "configs" / "model_presets.yaml")


@pytest.fixture(autouse=True)
def _clear_preset_cache():
    # lru_cache on load_presets — clear between tests.
    load_presets.cache_clear()
    yield
    load_presets.cache_clear()


class TestPresetCatalog:
    def test_five_presets_present(self):
        names = list_presets(PRESETS_PATH)
        assert {"inverter_10kHz", "inverter_50kHz", "obc_clllc",
                "motor_drive", "microgrid"} <= set(names)

    def test_required_keys_in_each(self):
        required = {"sim_step", "gds_oversampling", "device"}
        for name, p in load_presets(PRESETS_PATH).items():
            assert required <= p.keys(), f"preset {name} missing keys"

    def test_lookup_hit(self):
        p = get_preset("obc_clllc", PRESETS_PATH)
        assert p is not None
        assert p["gds_oversampling"] == "switch_level"

    def test_lookup_miss(self):
        assert get_preset("nope", PRESETS_PATH) is None

    def test_missing_file_returns_empty(self, tmp_path):
        fake = tmp_path / "nope.yaml"
        assert load_presets(str(fake)) == {}


class TestLoadModelPresetIntegration:
    async def test_preset_merge_emits_event(self, tmp_path):
        cfg = {"model": {"path": "models/test.tse", "preset": "inverter_10kHz"}}
        cfg_path = tmp_path / "model.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg))

        state = {
            "goal": "test",
            "config_path": str(cfg_path),
            "model_path": "",
            "model_signals": [],
            "model_loaded": False,
            "device_mode": "",
            "active_preset": "",
            "rag_context": "",
            "events": [],
        }

        # Patch out HIL + RAG I/O and force the preset path to point at the
        # real configs/model_presets.yaml (via module-level DEFAULT_PATH).
        with patch("src.nodes.load_model.get_hil") as mhil, \
             patch("src.nodes.load_model.get_rag") as mrag, \
             patch("src.nodes.load_model.get_preset") as mgp:
            mhil.return_value = AsyncMock()
            mhil.return_value.execute = AsyncMock(return_value={"signals": []})
            mrag.return_value = AsyncMock()
            mrag.return_value.execute = AsyncMock(return_value={"results": []})
            mgp.return_value = {"sim_step": 1e-6, "device": "HIL604"}

            result = await load_model(state)

        assert result["active_preset"] == "inverter_10kHz"
        messages = [e["message"] for e in result["events"]]
        assert any("Applied model preset" in m for m in messages)

    async def test_unknown_preset_warns(self, tmp_path):
        cfg = {"model": {"path": "models/test.tse", "preset": "nope"}}
        cfg_path = tmp_path / "model.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg))

        state = {
            "goal": "test",
            "config_path": str(cfg_path),
            "model_path": "",
            "model_signals": [],
            "model_loaded": False,
            "device_mode": "",
            "active_preset": "",
            "rag_context": "",
            "events": [],
        }

        with patch("src.nodes.load_model.get_hil") as mhil, \
             patch("src.nodes.load_model.get_rag") as mrag, \
             patch("src.nodes.load_model.get_preset", return_value=None):
            mhil.return_value = AsyncMock()
            mhil.return_value.execute = AsyncMock(return_value={"signals": []})
            mrag.return_value = AsyncMock()
            mrag.return_value.execute = AsyncMock(return_value={"results": []})

            result = await load_model(state)

        assert result["active_preset"] == ""
        messages = [e["message"] for e in result["events"]]
        assert any("Unknown preset" in m for m in messages)


class TestLoadModelDeviceMode:
    async def test_vhil_mock_mode_when_typhoon_missing(self, tmp_path):
        cfg_path = tmp_path / "model.yaml"
        cfg_path.write_text(yaml.safe_dump({"model": {"path": "x.tse"}}))

        state = {
            "goal": "test",
            "config_path": str(cfg_path),
            "model_path": "",
            "model_signals": [],
            "model_loaded": False,
            "device_mode": "",
            "active_preset": "",
            "rag_context": "",
            "events": [],
        }

        with patch("src.nodes.load_model.HAS_TYPHOON", False), \
             patch("src.nodes.load_model.get_hil") as mhil, \
             patch("src.nodes.load_model.get_rag") as mrag:
            mhil.return_value = AsyncMock()
            mhil.return_value.execute = AsyncMock(return_value={"signals": []})
            mrag.return_value = AsyncMock()
            mrag.return_value.execute = AsyncMock(return_value={"results": []})

            result = await load_model(state)

        assert result["device_mode"] == "vhil_mock"
        assert any(
            "Device mode" in e["message"] and "vhil_mock" in e["message"]
            for e in result["events"]
        )
