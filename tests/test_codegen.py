"""
Tests for the HTAF code generation pipeline.
Covers: graph structure, TSE parsing, requirement mapping,
code generation, validation, and export.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from src.graph_codegen import build_codegen_graph, compile_codegen_graph
from src.nodes.export_tests import export_tests
from src.nodes.generate_tests import generate_tests
from src.nodes.map_requirements import map_requirements
from src.nodes.parse_tse import parse_tse
from src.nodes.validate_code import validate_code
from src.state import ParsedTSE, VerificationRequirement, make_event


# ---------------------------------------------------------------------------
# Fixtures: sample TSE content
# ---------------------------------------------------------------------------

SAMPLE_DSL_TSE = """\
version = 4.2
model_name = "dab standard"

[configuration]
simulation_time_step = 2e-7
dsp_timer_periods = 100e-6

[component "Vout"]
type = "core/Voltage Measurement"

[component "Iin"]
type = "core/Current Measurement"

[component "PWM Enable"]
type = "core/Digital Probe"

[component "Vin"]
type = "core/Voltage Source"
init_const_value = 35.0

[component "Reference"]
type = "core/SCADA Input"
def_value = 50.0

[component "PWM Enable SCADA"]
type = "core/SCADA Input"
def_value = 1.0

[component "DAB Converter"]
type = "core/DAB"

CODE model_init {
    Kp = 0.5
    Ki = 100
    Ts = 100e-6
    f_sw = 50000
}
"""

SAMPLE_XML_TSE = """\
<?xml version="1.0"?>
<model name="boost_converter">
  <configuration>
    <property name="simulation_time_step" value="1e-6"/>
    <property name="dsp_timer_periods" value="50e-6"/>
  </configuration>
  <component name="Vout" type="core/Voltage Measurement"/>
  <component name="Iout" type="core/Current Measurement"/>
  <component name="Vin" type="core/Voltage Source">
    <property name="init_const_value" value="48.0"/>
  </component>
  <component name="Ref" type="core/SCADA Input">
    <property name="def_value" value="400.0"/>
  </component>
  <component name="Boost Stage" type="core/Boost"/>
</model>
"""


def _state(**overrides):
    """Create a minimal codegen state for testing."""
    defaults = {
        "goal": "",
        "config_path": "",
        "model_path": "",
        "model_signals": [],
        "model_loaded": False,
        "rag_context": "",
        "plan_strategy": "",
        "scenarios": [],
        "scenario_index": 0,
        "estimated_duration_s": 0,
        "standard_coverage": {},
        "results": [],
        "current_scenario": None,
        "diagnosis": None,
        "heal_retry_count": 0,
        "events": [],
        "report_path": "",
        "error": "",
        "tse_content": "",
        "tse_path": "",
        "parsed_tse": None,
        "test_requirements": [],
        "generated_files": {},
        "codegen_validation": None,
        "export_path": "",
        "codegen_mode": "mock",
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Graph structure tests
# ---------------------------------------------------------------------------

class TestCodegenGraphStructure:
    def test_graph_compiles(self):
        app = compile_codegen_graph()
        assert app is not None

    def test_graph_has_all_nodes(self):
        g = build_codegen_graph()
        app = g.compile()
        node_ids = set(app.get_graph().nodes)
        expected = {"parse_tse", "map_requirements", "generate_tests", "validate_code", "export_tests"}
        assert expected.issubset(node_ids)

    def test_entry_point_is_parse_tse(self):
        g = build_codegen_graph()
        app = g.compile()
        graph_repr = app.get_graph().draw_mermaid()
        assert "parse_tse" in graph_repr


# ---------------------------------------------------------------------------
# Node: parse_tse
# ---------------------------------------------------------------------------

class TestParseTse:
    async def test_dsl_format(self):
        result = await parse_tse(_state(tse_content=SAMPLE_DSL_TSE, tse_path="dab.tse"))
        parsed = result["parsed_tse"]
        assert parsed["topology"] == "dab"
        assert parsed["fmt"] == "dsl"
        assert "Vout" in parsed["analog_signals"]
        assert "Iin" in parsed["analog_signals"]
        assert "PWM Enable" in parsed["digital_signals"]
        assert parsed["sources"]["Vin"] == 35.0
        assert parsed["scada_inputs"]["Reference"] == 50.0
        assert parsed["sim_time_step"] == 2e-7
        assert parsed["control_params"]["Kp"] == 0.5

    async def test_xml_format(self):
        result = await parse_tse(_state(tse_content=SAMPLE_XML_TSE, tse_path="boost.tse"))
        parsed = result["parsed_tse"]
        assert parsed["topology"] == "boost"
        assert parsed["fmt"] == "xml"
        assert "Vout" in parsed["analog_signals"]
        assert parsed["sources"]["Vin"] == 48.0

    async def test_empty_content_returns_error(self):
        result = await parse_tse(_state(tse_content="", tse_path="empty.tse"))
        assert "error" in result
        assert result["events"][0]["event_type"] == "error"

    async def test_event_emitted(self):
        result = await parse_tse(_state(tse_content=SAMPLE_DSL_TSE, tse_path="test.tse"))
        assert len(result["events"]) == 1
        assert result["events"][0]["event_type"] == "observation"


# ---------------------------------------------------------------------------
# Node: map_requirements
# ---------------------------------------------------------------------------

class TestMapRequirements:
    async def test_dab_topology(self):
        parsed = ParsedTSE(
            model_name="dab", topology="dab",
            sources={"Vin": 35.0}, analog_signals=["Vout"],
        ).model_dump()
        result = await map_requirements(_state(parsed_tse=parsed))
        reqs = result["test_requirements"]
        names = [r["name"] for r in reqs]
        assert "output_voltage_regulation" in names
        assert "phase_shift_control" in names
        assert "soft_switching_verification" in names

    async def test_boost_topology(self):
        parsed = ParsedTSE(
            model_name="boost", topology="boost",
            sources={"Vin": 48.0}, analog_signals=["Vout"],
        ).model_dump()
        result = await map_requirements(_state(parsed_tse=parsed))
        names = [r["name"] for r in result["test_requirements"]]
        assert "output_ripple" in names
        assert "settling_time" in names

    async def test_unknown_topology_common_only(self):
        parsed = ParsedTSE(topology="unknown", sources={"Vin": 12.0}).model_dump()
        result = await map_requirements(_state(parsed_tse=parsed))
        reqs = result["test_requirements"]
        assert len(reqs) == 1
        assert reqs[0]["name"] == "output_voltage_regulation"

    async def test_no_parsed_tse_returns_error(self):
        result = await map_requirements(_state(parsed_tse=None))
        assert "error" in result


# ---------------------------------------------------------------------------
# Node: generate_tests
# ---------------------------------------------------------------------------

class TestGenerateTests:
    async def test_mock_mode(self):
        parsed = ParsedTSE(topology="boost", sources={"Vin": 48.0}, analog_signals=["Vout"]).model_dump()
        reqs = [VerificationRequirement(req_id="R1", name="regulation", metric="output_voltage", target_value=48.0).model_dump()]
        result = await generate_tests(_state(parsed_tse=parsed, test_requirements=reqs, codegen_mode="mock"))
        files = result["generated_files"]
        assert "test_mock_boost.py" in files
        assert "MockHilRunner" in files["test_mock_boost.py"]

    async def test_typhoon_mode(self):
        parsed = ParsedTSE(
            topology="dab", model_name="dab_test",
            sources={"Vin": 35.0}, scada_inputs={"Reference": 50.0},
            analog_signals=["Vout"], control_params={"Kp": 0.5},
        ).model_dump()
        reqs = [
            VerificationRequirement(req_id="R1", name="regulation", metric="output_voltage", target_value=50.0).model_dump(),
            VerificationRequirement(req_id="R2", name="ripple", metric="ripple", target_value=0.02, topology_specific=True).model_dump(),
        ]
        result = await generate_tests(_state(parsed_tse=parsed, test_requirements=reqs, codegen_mode="typhoon"))
        files = result["generated_files"]
        assert "constants.py" in files
        assert "conftest.py" in files
        assert "pytest.ini" in files
        assert "test_dab.py" in files
        assert "utils/analysis.py" in files
        assert "MODEL_PATH" in files["constants.py"]
        assert "def ts(" in files["constants.py"]

    async def test_no_parsed_tse_returns_error(self):
        result = await generate_tests(_state(parsed_tse=None))
        assert "error" in result


# ---------------------------------------------------------------------------
# Node: validate_code
# ---------------------------------------------------------------------------

class TestValidateCode:
    async def test_clean_code_passes(self):
        files = {
            "test_boost.py": "import pytest\n\ndef test_foo():\n    assert True\n",
            "constants.py": "MODEL_PATH = 'x.tse'\ndef ts(n=1): return 0.001 * n\n",
        }
        result = await validate_code(_state(generated_files=files, codegen_mode="typhoon"))
        v = result["codegen_validation"]
        assert v["valid"] is True

    async def test_forbidden_hil_connect(self):
        files = {"test_bad.py": "import hil\nhil.connect()\n"}
        result = await validate_code(_state(generated_files=files))
        v = result["codegen_validation"]
        assert v["valid"] is False
        assert any("hil.connect()" in e for e in v["errors"])

    async def test_forbidden_session_scope(self):
        files = {"conftest.py": '@pytest.fixture(scope="session")\ndef setup(): pass\n'}
        result = await validate_code(_state(generated_files=files))
        v = result["codegen_validation"]
        assert v["valid"] is False
        assert any("session" in e for e in v["errors"])

    async def test_forbidden_time_sleep(self):
        files = {"test_x.py": "import time\ntime.sleep(1)\n"}
        result = await validate_code(_state(generated_files=files))
        v = result["codegen_validation"]
        assert v["valid"] is False
        assert any("time.sleep" in e for e in v["errors"])

    async def test_syntax_error_detected(self):
        files = {"test_bad.py": "def foo(\n"}
        result = await validate_code(_state(generated_files=files))
        v = result["codegen_validation"]
        assert v["valid"] is False
        assert any("Syntax error" in e for e in v["errors"])

    async def test_no_files_is_invalid(self):
        result = await validate_code(_state(generated_files={}))
        v = result["codegen_validation"]
        assert v["valid"] is False


# ---------------------------------------------------------------------------
# Node: export_tests
# ---------------------------------------------------------------------------

class TestExportTests:
    async def test_export_creates_files(self, tmp_path, monkeypatch):
        import src.nodes.export_tests as mod
        monkeypatch.setattr(mod, "OUTPUT_DIR", tmp_path)
        files = {"test_boost.py": "# test\n", "constants.py": "X = 1\n"}
        validation = {"valid": True, "errors": [], "warnings": []}
        parsed = {"topology": "boost"}
        result = await export_tests(_state(
            generated_files=files,
            codegen_validation=validation,
            parsed_tse=parsed,
        ))
        assert result.get("export_path")
        export_dir = Path(result["export_path"])
        assert export_dir.exists()
        assert (export_dir / "test_boost.py").exists()
        # Check ZIP
        zips = list(tmp_path.glob("*.zip"))
        assert len(zips) == 1

    async def test_export_skipped_on_invalid(self):
        validation = {"valid": False, "errors": ["some error"], "warnings": []}
        result = await export_tests(_state(
            generated_files={"test.py": "x"},
            codegen_validation=validation,
        ))
        assert "export_path" not in result or not result.get("export_path")
        assert result["events"][0]["event_type"] == "error"


# ---------------------------------------------------------------------------
# End-to-end: DSL TSE -> generated files
# ---------------------------------------------------------------------------

class TestEndToEnd:
    async def test_dsl_to_mock_tests(self):
        # Stage 1: parse
        s = _state(tse_content=SAMPLE_DSL_TSE, tse_path="dab.tse", codegen_mode="mock")
        r1 = await parse_tse(s)
        s.update(r1)

        # Stage 2: map
        r2 = await map_requirements(s)
        s.update(r2)

        # Stage 3: generate
        r3 = await generate_tests(s)
        s.update(r3)

        # Stage 4: validate
        r4 = await validate_code(s)
        s.update(r4)

        assert s["codegen_validation"]["valid"] is True
        assert "test_mock_dab.py" in s["generated_files"]
        assert "MockHilRunner" in s["generated_files"]["test_mock_dab.py"]
