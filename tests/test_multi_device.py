"""Tests for Phase 4-I multi-device HIL support."""

from __future__ import annotations

import asyncio

import pytest

from src.nodes.load_model import _dut_singletons, get_dut
from src.tools.dut import HILBackend, MockBackend
from src.tools.dut.base import _DEVICE_LOCKS, get_hardware_lock


@pytest.fixture(autouse=True)
def _reset_caches():
    """Each test gets a clean lock registry + backend cache."""
    _DEVICE_LOCKS.clear()
    _dut_singletons.clear()
    yield
    _DEVICE_LOCKS.clear()
    _dut_singletons.clear()


# ---------------------------------------------------------------------------
# Per-device lock registry
# ---------------------------------------------------------------------------

class TestLockRegistry:
    def test_default_lock_is_lazy(self):
        # Cache cleared by fixture; first call creates the lock.
        assert "default" not in _DEVICE_LOCKS
        get_hardware_lock("default")
        assert "default" in _DEVICE_LOCKS

    def test_same_id_returns_same_lock(self):
        a = get_hardware_lock("hil_a")
        b = get_hardware_lock("hil_a")
        assert a is b

    def test_different_ids_get_independent_locks(self):
        a = get_hardware_lock("hil_a")
        b = get_hardware_lock("hil_b")
        assert a is not b


# ---------------------------------------------------------------------------
# Backend.device_id wiring
# ---------------------------------------------------------------------------

class TestBackendDeviceId:
    def test_default_device_id(self):
        b = MockBackend()
        assert b.device_id == "default"

    def test_explicit_device_id_in_config(self):
        b = MockBackend(config={"device_id": "rig_42"})
        assert b.device_id == "rig_42"

    def test_lock_method_returns_per_device_lock(self):
        a = MockBackend(config={"device_id": "rig_a"})
        b = MockBackend(config={"device_id": "rig_b"})
        assert a.lock() is not b.lock()
        # Same device id, different backend instances -> shared lock.
        c = MockBackend(config={"device_id": "rig_a"})
        assert a.lock() is c.lock()

    def test_hil_backend_inherits_device_id(self):
        h = HILBackend(config={"device_id": "hil_404_a"})
        assert h.device_id == "hil_404_a"


# ---------------------------------------------------------------------------
# get_dut routing per scenario
# ---------------------------------------------------------------------------

class TestGetDutRouting:
    def test_scenario_device_id_overrides_default(self):
        state = {
            "dut_backend": "mock",
            "dut_config": {},
            "device_pool": {"rig_a": {}, "rig_b": {}},
        }
        a = get_dut(state, scenario={"device_id": "rig_a"})
        b = get_dut(state, scenario={"device_id": "rig_b"})
        assert a is not b
        assert a.device_id == "rig_a"
        assert b.device_id == "rig_b"

    def test_no_scenario_uses_default(self):
        state = {"dut_backend": "mock", "dut_config": {}}
        b = get_dut(state)
        assert b.device_id == "default"

    def test_pool_overlay_merges_into_config(self):
        state = {
            "dut_backend": "mock",
            "dut_config": {"shared_setting": 1},
            "device_pool": {"rig_a": {"a2l_path": "a.a2l"}},
        }
        b = get_dut(state, scenario={"device_id": "rig_a"})
        assert b.config["shared_setting"] == 1
        assert b.config["a2l_path"] == "a.a2l"
        assert b.config["device_id"] == "rig_a"

    def test_caching_per_device(self):
        state = {"dut_backend": "mock", "dut_config": {}, "device_pool": {}}
        a1 = get_dut(state, scenario={"device_id": "rig_a"})
        a2 = get_dut(state, scenario={"device_id": "rig_a"})
        assert a1 is a2


# ---------------------------------------------------------------------------
# Cross-device parallel: different devices DO overlap, same device DOES NOT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_different_devices_overlap():
    """Two coroutines on different devices run concurrently."""
    order: list[str] = []
    lock_a = get_hardware_lock("rig_a")
    lock_b = get_hardware_lock("rig_b")

    async def task(name: str, lock):
        async with lock:
            order.append(f"{name}_in")
            await asyncio.sleep(0.05)
            order.append(f"{name}_out")

    await asyncio.gather(task("A", lock_a), task("B", lock_b))
    # Both should have entered before either exited.
    a_in = order.index("A_in")
    b_in = order.index("B_in")
    a_out = order.index("A_out")
    b_out = order.index("B_out")
    assert a_in < a_out and b_in < b_out  # each ran
    # Interleaving proof: at least one task entered before the other
    # exited (impossible if locks shared).
    assert a_in < b_out and b_in < a_out


@pytest.mark.asyncio
async def test_same_device_serializes():
    """Two coroutines targeting the same device run one-at-a-time."""
    order: list[str] = []
    lock = get_hardware_lock("rig_shared")

    async def task(name: str):
        async with lock:
            order.append(f"{name}_in")
            await asyncio.sleep(0.02)
            order.append(f"{name}_out")

    await asyncio.gather(task("A"), task("B"))
    assert order in (
        ["A_in", "A_out", "B_in", "B_out"],
        ["B_in", "B_out", "A_in", "A_out"],
    )


# ---------------------------------------------------------------------------
# Scenario YAML round-trip: device_id propagates through plan_tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_tests_reads_device_id_from_yaml(tmp_path):
    cfg = tmp_path / "model.yaml"
    cfg.write_text(
        """
model:
  path: dummy.tse
scenarios:
  rig_a_test:
    description: rig A scenario
    device_id: rig_a
    parameters: {target_cell: 1, test_voltage: 3.6, hold_duration_s: 0.01}
    measurements: [V_cell_1]
    pass_fail_rules: {}
  rig_b_test:
    description: rig B scenario
    device_id: rig_b
    parameters: {target_cell: 1, test_voltage: 3.6, hold_duration_s: 0.01}
    measurements: [V_cell_1]
    pass_fail_rules: {}
""",
        encoding="utf-8",
    )
    from src.nodes.plan_tests import _load_predefined_scenarios

    scenarios = _load_predefined_scenarios(str(cfg))
    by_id = {s["scenario_id"]: s for s in scenarios}
    assert by_id["rig_a_test"]["device_id"] == "rig_a"
    assert by_id["rig_b_test"]["device_id"] == "rig_b"
