"""apply_fix node — full-coverage tests for the calibration-commit path.

The pre-existing suite only exercised the rejection paths (validator
veto, missing diagnosis). This file pushes coverage on
``src/nodes/apply_fix.py`` from 25% to >=80% by walking every
documented branch:

    1. action_type != "xcp_calibration"  -> heal_retry_count++ + no-op event
    2. missing param / value             -> same no-op
    3. validator BLOCKED (whitelist)     -> error event, no DUT call
    4. happy path: validator OK -> dut.write_calibration -> success event
    5. backend returns ``error`` field   -> error message in event
    6. twin_enabled -> get_twin().commit mirror sync after successful write
    7. multi-device routing: scenario.device_id propagates to get_dut
    8. get_xcp() backward-compat accessor across backends

These are unit tests with a fake DUT; the LangGraph integration is
already covered in test_e2e_phase4 / test_parallel_hitl.
"""

from __future__ import annotations

import pytest

from src.constants import ACTION_XCP_CALIBRATION
from src.nodes import apply_fix as af_module
from src.nodes.apply_fix import apply_fix, get_xcp


# ---------------------------------------------------------------------------
# Fake DUT backend captured by ``monkeypatch.setattr(af_module, "get_dut", ...)``
# ---------------------------------------------------------------------------

class _FakeDUT:
    """Minimal stand-in for a DUTBackend that records every call."""

    def __init__(self, write_result: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.last_scenario: dict | None = None
        self._write_result = write_result or {
            "variable": "", "written_value": None, "status": "ok",
        }

    async def write_calibration(self, param: str, value: float) -> dict:
        self.calls.append({"param": param, "value": value})
        # Echo the input into the result so tests can verify routing.
        return {**self._write_result,
                "variable": param, "written_value": value}


def _install_fake_dut(monkeypatch, fake: _FakeDUT) -> _FakeDUT:
    """Override apply_fix's ``get_dut`` to return ``fake`` and capture
    the scenario kwarg so multi-device routing can be asserted."""
    def _get_dut(state, *, scenario=None):
        fake.last_scenario = dict(scenario) if scenario is not None else None
        return fake
    monkeypatch.setattr(af_module, "get_dut", _get_dut)
    return fake


# ---------------------------------------------------------------------------
# Branch 1+2: no-op early returns
# ---------------------------------------------------------------------------

class TestNoOpReturns:
    @pytest.mark.asyncio
    async def test_action_type_not_calibration(self):
        state = {
            "diagnosis": {"corrective_action_type": "retest",
                           "corrective_param": "J",
                           "corrective_value": 0.35},
        }
        out = await apply_fix(state)
        assert out["heal_retry_count"] == 1
        assert any("No XCP fix" in e["message"] for e in out["events"])

    @pytest.mark.asyncio
    async def test_missing_param(self):
        state = {
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "",
                           "corrective_value": 0.35},
        }
        out = await apply_fix(state)
        assert any("No XCP fix" in e["message"] for e in out["events"])

    @pytest.mark.asyncio
    async def test_missing_value(self):
        state = {
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": None},
        }
        out = await apply_fix(state)
        assert any("No XCP fix" in e["message"] for e in out["events"])

    @pytest.mark.asyncio
    async def test_no_diagnosis_at_all(self):
        out = await apply_fix({})
        assert out["heal_retry_count"] == 1


# ---------------------------------------------------------------------------
# Branch 3: validator blocks non-whitelisted writes
# ---------------------------------------------------------------------------

class TestValidatorBlocked:
    @pytest.mark.asyncio
    async def test_non_whitelisted_param_blocked(self, monkeypatch):
        fake = _FakeDUT()
        _install_fake_dut(monkeypatch, fake)

        out = await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "ARBITRARY_DANGER_FLAG",
                           "corrective_value": 1.0},
        })
        # Validator vetoes -> we record an error event and never call DUT.
        assert any("BLOCKED" in e["message"] for e in out["events"])
        assert fake.calls == [], "DUT must not be called when blocked"
        assert out["heal_retry_count"] == 1


# ---------------------------------------------------------------------------
# Branch 4: happy path -- whitelisted write reaches the DUT
# ---------------------------------------------------------------------------

class TestHappyWrite:
    @pytest.mark.asyncio
    async def test_writes_to_dut(self, monkeypatch):
        fake = _FakeDUT()
        _install_fake_dut(monkeypatch, fake)

        out = await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": 0.35},
            "heal_retry_count": 0,
        })
        assert fake.calls == [{"param": "J", "value": 0.35}]
        assert out["heal_retry_count"] == 1
        msg = out["events"][0]["message"]
        assert "XCP write: J = 0.35" in msg
        assert "retry #1" in msg

    @pytest.mark.asyncio
    async def test_retry_count_increments(self, monkeypatch):
        fake = _FakeDUT()
        _install_fake_dut(monkeypatch, fake)

        out = await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": 0.4},
            "heal_retry_count": 2,  # already retried twice
        })
        assert out["heal_retry_count"] == 3
        assert "retry #3" in out["events"][0]["message"]


# ---------------------------------------------------------------------------
# Branch 5: backend returns an ``error`` field
# ---------------------------------------------------------------------------

class TestBackendError:
    @pytest.mark.asyncio
    async def test_error_propagated_to_event(self, monkeypatch):
        fake = _FakeDUT(write_result={
            "error": "BLOCKED: OVP threshold not in writable list",
            "blocked": True,
        })
        _install_fake_dut(monkeypatch, fake)

        out = await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": 0.35},
        })
        # Backend got the call -- the message reports the failure.
        assert fake.calls == [{"param": "J", "value": 0.35}]
        assert "XCP write failed" in out["events"][0]["message"]


# ---------------------------------------------------------------------------
# Branch 6: twin mirror sync
# ---------------------------------------------------------------------------

class TestTwinMirrorSync:
    @pytest.mark.asyncio
    async def test_commit_called_when_twin_enabled(self, monkeypatch):
        fake = _FakeDUT()
        _install_fake_dut(monkeypatch, fake)
        from src.twin import get_twin, reset_twin
        reset_twin()

        out = await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": 0.45},
            "twin_enabled": True,
            "current_scenario": {"scenario_id": "vsm_x"},
        })
        # The twin's calibration mirror is now in sync with what was
        # just written -- subsequent simulate_fix can detect no-ops.
        twin = get_twin()
        assert twin.state.get("J") == pytest.approx(0.45)
        # History records the (param, value) for repeat-attempt detection.
        assert ("J", 0.45) in twin.history.get("vsm_x", [])
        # Event still reports success.
        assert "XCP write: J = 0.45" in out["events"][0]["message"]

    @pytest.mark.asyncio
    async def test_commit_skipped_when_twin_disabled(self, monkeypatch):
        fake = _FakeDUT()
        _install_fake_dut(monkeypatch, fake)
        from src.twin import get_twin, reset_twin
        reset_twin()

        await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": 0.45},
            # twin_enabled defaults to False
            "current_scenario": {"scenario_id": "vsm_x"},
        })
        # Twin state untouched.
        assert get_twin().state.get("J") is None

    @pytest.mark.asyncio
    async def test_commit_skipped_when_backend_errored(self, monkeypatch):
        fake = _FakeDUT(write_result={"error": "downstream HIL crash"})
        _install_fake_dut(monkeypatch, fake)
        from src.twin import get_twin, reset_twin
        reset_twin()

        await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": 0.45},
            "twin_enabled": True,
            "current_scenario": {"scenario_id": "vsm_x"},
        })
        # Backend failed -> twin must NOT have been told the write succeeded.
        assert get_twin().state.get("J") is None


# ---------------------------------------------------------------------------
# Branch 7: multi-device routing
# ---------------------------------------------------------------------------

class TestMultiDeviceRouting:
    @pytest.mark.asyncio
    async def test_scenario_device_id_passed_to_get_dut(self, monkeypatch):
        fake = _FakeDUT()
        _install_fake_dut(monkeypatch, fake)

        await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": 0.35},
            "current_scenario": {"scenario_id": "rig_a_test",
                                  "device_id": "hil_404_a"},
        })
        # ``apply_fix`` forwarded the scenario dict so get_dut routes to
        # the right per-device backend instance (Phase 4-I).
        assert fake.last_scenario is not None
        assert fake.last_scenario.get("device_id") == "hil_404_a"

    @pytest.mark.asyncio
    async def test_no_scenario_falls_back(self, monkeypatch):
        fake = _FakeDUT()
        _install_fake_dut(monkeypatch, fake)

        await apply_fix({
            "diagnosis": {"corrective_action_type": ACTION_XCP_CALIBRATION,
                           "corrective_param": "J",
                           "corrective_value": 0.35},
        })
        # Scenario is empty {} — get_dut still gets the kwarg, just
        # without a device_id (default device routing kicks in).
        assert fake.last_scenario == {}


# ---------------------------------------------------------------------------
# Branch 8: get_xcp() backward-compat accessor
# ---------------------------------------------------------------------------

class TestGetXcpAccessor:
    def test_returns_xcp_executor_for_xcp_backend(self, monkeypatch):
        from src.tools.xcp_tools import XCPToolExecutor
        from src.tools.dut import XCPBackend

        backend = XCPBackend(config={"a2l_path": "dummy.a2l"})

        def _get_dut(_state, **__):
            return backend

        monkeypatch.setattr(af_module, "get_dut", _get_dut)
        executor = get_xcp()
        assert isinstance(executor, XCPToolExecutor)

    def test_returns_fresh_executor_for_non_xcp_backend(self, monkeypatch):
        from src.tools.xcp_tools import XCPToolExecutor

        class _NoXcpBackend:
            xcp = None

        def _get_dut(_state, **__):
            return _NoXcpBackend()

        monkeypatch.setattr(af_module, "get_dut", _get_dut)
        executor = get_xcp()
        assert isinstance(executor, XCPToolExecutor)
