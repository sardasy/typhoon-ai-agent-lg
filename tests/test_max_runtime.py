"""Tests for the P1-5 walltime cap (--max-runtime-s / THAA_MAX_RUNTIME_S)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from main import _parse_max_runtime, run_cli


class TestParseMaxRuntime:
    def test_cli_value_wins_over_env(self):
        assert _parse_max_runtime(10.0, "999") == 10.0

    def test_env_used_when_cli_none(self):
        assert _parse_max_runtime(None, "30") == 30.0

    def test_both_unset_returns_none(self):
        assert _parse_max_runtime(None, None) is None
        assert _parse_max_runtime(None, "") is None

    def test_zero_means_off(self):
        """GNU `timeout` convention: 0 disables the cap."""
        assert _parse_max_runtime(0, None) is None
        assert _parse_max_runtime(None, "0") is None

    def test_negative_means_off(self):
        assert _parse_max_runtime(-5.0, None) is None

    def test_malformed_env_ignored(self, capsys):
        result = _parse_max_runtime(None, "not-a-number")
        assert result is None
        captured = capsys.readouterr()
        assert "THAA_MAX_RUNTIME_S" in captured.err


class _FakeApp:
    """Minimal app stub exposing `astream` + `checkpointer`."""

    def __init__(self, per_step_delay: float, num_steps: int = 100):
        self._delay = per_step_delay
        self._steps = num_steps
        self.checkpointer = None

    async def astream(self, input_value, config=None):
        for i in range(self._steps):
            await asyncio.sleep(self._delay)
            yield {f"node_{i}": {"events": []}}


class TestRunCliDeadline:
    """Integration tests for the astream-loop deadline check.

    run_cli opens a raw-stdout rich Console which collides with pytest's FD
    capture; each test uses ``capfd.disabled()`` so pytest hands back the real
    stdout for the duration of the run.
    """

    @pytest.mark.asyncio
    async def test_timeout_fires_between_node_yields(self, capfd):
        """Deadline check runs at node boundary; cap ~0.1s against 10x0.05s steps."""
        fake = _FakeApp(per_step_delay=0.05, num_steps=10)
        with capfd.disabled(), patch("src.graph.compile_graph", return_value=fake):
            with pytest.raises(TimeoutError, match="Walltime cap"):
                await run_cli(
                    goal="test",
                    config_path="configs/model.yaml",
                    max_runtime_s=0.1,
                )

    @pytest.mark.asyncio
    async def test_no_cap_runs_to_completion(self, capfd):
        """With max_runtime_s=None, run completes without raising."""
        fake = _FakeApp(per_step_delay=0.001, num_steps=3)
        with capfd.disabled(), patch("src.graph.compile_graph", return_value=fake):
            await run_cli(
                goal="test",
                config_path="configs/model.yaml",
                max_runtime_s=None,
            )

    @pytest.mark.asyncio
    async def test_cap_not_exceeded_runs_normally(self, capfd):
        """Fast run under the cap finishes cleanly."""
        fake = _FakeApp(per_step_delay=0.001, num_steps=3)
        with capfd.disabled(), patch("src.graph.compile_graph", return_value=fake):
            await run_cli(
                goal="test",
                config_path="configs/model.yaml",
                max_runtime_s=5.0,
            )
