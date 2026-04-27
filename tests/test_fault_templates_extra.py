"""Coverage boost for ``src/fault_templates.py`` -- the 5 templates
not exercised by ``test_fault_templates.py`` plus validation paths.

The pre-existing suite covers ``overvoltage``, ``undervoltage``,
``short_circuit``, ``open_circuit``, ``frequency_deviation`` and the
registry/dispatch surface. This file adds:

  - ``voltage_sag`` / ``voltage_swell``       (LVRT / HVRT)
  - ``vsm_steady_state`` / ``vsm_pref_step``  (IEEE 2800)
  - ``phase_jump``                            (IEEE 2800 §7.3)
  - validation: out-of-range params raise ValueError
  - ``_safe_sleep`` honors the hard_max cap
"""

from __future__ import annotations

import pytest

from src.fault_templates import _safe_sleep, get_template


# ---------------------------------------------------------------------------
# Recording stub for the dut/hil "execute" surface
# ---------------------------------------------------------------------------

class _RecordingDUT:
    """Captures every ``execute(tool, payload)`` call. Mirrors the
    minimal surface the templates rely on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, tool: str, payload: dict) -> dict:
        self.calls.append((tool, dict(payload)))
        return {"status": "ok"}

    def writes_for(self, signal: str) -> list[dict]:
        """Convenience: every signal-write payload that targeted
        ``signal``."""
        return [
            p for t, p in self.calls
            if t == "hil_signal_write" and p.get("signal") == signal
        ]


@pytest.fixture(autouse=True)
def _zero_sleep(monkeypatch):
    """Skip the long ``asyncio.sleep`` calls inside templates so the
    suite stays fast. ``_safe_sleep`` already enforces a max cap; we
    just zero out the actual await."""
    import asyncio
    async def _fast(_seconds):
        return None
    monkeypatch.setattr(asyncio, "sleep", _fast)


# ---------------------------------------------------------------------------
# voltage_sag (LVRT)
# ---------------------------------------------------------------------------

class TestVoltageSag:
    @pytest.mark.asyncio
    async def test_three_phase_pre_sag_post_sequence(self):
        tmpl = get_template("voltage_sag")
        assert tmpl is not None
        dut = _RecordingDUT()
        await tmpl.apply(dut, {
            "signal_ac_sources": ["Vsa", "Vsb", "Vsc"],
            "nominal_voltage_peak": 325.27,
            "sag_voltage_pu": 0.5,
            "pre_fault_duration_s": 0.01,
            "fault_duration_s": 0.01,
            "post_fault_duration_s": 0.01,
        })
        # 3 phases × 3 stages (pre / sag / post) = 9 sine writes.
        sines = [p for t, p in dut.calls
                  if t == "hil_signal_write" and p.get("waveform") == "sine"]
        assert len(sines) == 9
        # Phases must be 0 / 120 / 240 in each stage.
        phase_set = {p["phase_deg"] for p in sines}
        assert phase_set == {0, 120, 240}
        # Sag stage uses 50% of nominal.
        sag_writes = [p for p in sines
                       if p["value"] == pytest.approx(325.27 * 0.5)]
        assert len(sag_writes) == 3

    @pytest.mark.asyncio
    async def test_out_of_range_raises(self):
        tmpl = get_template("voltage_sag")
        with pytest.raises(ValueError, match="sag_voltage_pu"):
            await tmpl.apply(_RecordingDUT(), {
                "signal_ac_sources": ["Vsa"],
                "sag_voltage_pu": 1.5,  # >1.0 not allowed
            })

    @pytest.mark.asyncio
    async def test_single_signal_default(self):
        # When ``signal`` is given instead of signal_ac_sources, only
        # one phase is driven.
        tmpl = get_template("voltage_sag")
        dut = _RecordingDUT()
        await tmpl.apply(dut, {
            "signal": "Vgrid",
            "sag_voltage_pu": 0.7,
            "pre_fault_duration_s": 0.0, "fault_duration_s": 0.0,
            "post_fault_duration_s": 0.0,
        })
        # 1 signal × 3 stages = 3 sine writes
        sines = [p for t, p in dut.calls if p.get("waveform") == "sine"]
        assert len(sines) == 3
        assert all(p["signal"] == "Vgrid" for p in sines)


# ---------------------------------------------------------------------------
# voltage_swell (HVRT)
# ---------------------------------------------------------------------------

class TestVoltageSwell:
    @pytest.mark.asyncio
    async def test_swell_amplitude(self):
        tmpl = get_template("voltage_swell")
        dut = _RecordingDUT()
        await tmpl.apply(dut, {
            "signal_ac_sources": ["Vsa", "Vsb", "Vsc"],
            "nominal_voltage_peak": 325.27,
            "swell_voltage_pu": 1.15,
            "pre_fault_duration_s": 0.0, "fault_duration_s": 0.0,
            "post_fault_duration_s": 0.0,
        })
        sines = [p for t, p in dut.calls if p.get("waveform") == "sine"]
        # 3 phases × 3 stages = 9 sine writes
        assert len(sines) == 9
        # The middle stage has 1.15 × 325.27.
        swell_writes = [p for p in sines
                         if p["value"] == pytest.approx(325.27 * 1.15)]
        assert len(swell_writes) == 3

    @pytest.mark.asyncio
    async def test_out_of_range_raises(self):
        tmpl = get_template("voltage_swell")
        with pytest.raises(ValueError, match="swell_voltage_pu"):
            await tmpl.apply(_RecordingDUT(), {
                "signal_ac_sources": ["Vsa"],
                "swell_voltage_pu": 1.5,  # > 1.25 disallowed
            })

    @pytest.mark.asyncio
    async def test_swell_at_lower_bound_passes(self):
        # 1.00 pu is the inclusive lower bound -- valid (no swell, but
        # not rejected).
        tmpl = get_template("voltage_swell")
        await tmpl.apply(_RecordingDUT(), {
            "signal_ac_sources": ["Vsa"],
            "swell_voltage_pu": 1.0,
            "pre_fault_duration_s": 0.0, "fault_duration_s": 0.0,
            "post_fault_duration_s": 0.0,
        })  # no exception


# ---------------------------------------------------------------------------
# vsm_steady_state (IEEE 2800)
# ---------------------------------------------------------------------------

class TestVsmSteadyState:
    @pytest.mark.asyncio
    async def test_writes_all_scada_inputs(self):
        tmpl = get_template("vsm_steady_state")
        dut = _RecordingDUT()
        result = await tmpl.apply(dut, {
            "Pref_w": 5000.0, "Qref_var": -1000.0,
            "J": 0.3, "D": 10.0, "Kv": 1e-4,
            "settle_s": 0.0,
        })
        # Each SCADA input is written exactly once.
        signals = {p["signal"] for _, p in dut.calls}
        assert signals == {"P_ref", "Q_ref", "J", "D", "Kv"}
        assert result["P_ref"] == 5000.0
        assert result["Q_ref"] == -1000.0

    @pytest.mark.asyncio
    async def test_optional_tunables_skipped_when_absent(self):
        tmpl = get_template("vsm_steady_state")
        dut = _RecordingDUT()
        await tmpl.apply(dut, {
            "Pref_w": 1000.0, "Qref_var": 0.0,
            "settle_s": 0.0,
            # No J/D/Kv -- the writes for those keys must NOT be issued.
        })
        signals = {p["signal"] for _, p in dut.calls}
        assert "J" not in signals
        assert "D" not in signals
        assert "Kv" not in signals
        assert "P_ref" in signals
        assert "Q_ref" in signals


# ---------------------------------------------------------------------------
# vsm_pref_step
# ---------------------------------------------------------------------------

class TestVsmPrefStep:
    @pytest.mark.asyncio
    async def test_pref_written_twice_initial_then_step(self):
        tmpl = get_template("vsm_pref_step")
        dut = _RecordingDUT()
        await tmpl.apply(dut, {
            "Pref_initial_w": 1000.0,
            "Pref_step_w": 8000.0,
            "J": 0.5, "D": 12.0,
            "pre_step_s": 0.0, "capture_s": 0.0,
        })
        # P_ref takes two values during the run -- initial then step.
        pref_writes = dut.writes_for("P_ref")
        values = [p["value"] for p in pref_writes]
        assert values == [1000.0, 8000.0]

    @pytest.mark.asyncio
    async def test_default_J_D_when_absent(self):
        tmpl = get_template("vsm_pref_step")
        dut = _RecordingDUT()
        await tmpl.apply(dut, {
            "Pref_initial_w": 0, "Pref_step_w": 100,
            "pre_step_s": 0.0, "capture_s": 0.0,
        })
        # Defaults J=0.3, D=10.0 are written (template hard-codes them).
        j_writes = dut.writes_for("J")
        d_writes = dut.writes_for("D")
        assert j_writes and j_writes[0]["value"] == pytest.approx(0.3)
        assert d_writes and d_writes[0]["value"] == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# phase_jump (IEEE 2800 §7.3)
# ---------------------------------------------------------------------------

class TestPhaseJump:
    @pytest.mark.asyncio
    async def test_three_stage_phase_sequence(self):
        tmpl = get_template("phase_jump")
        dut = _RecordingDUT()
        await tmpl.apply(dut, {
            "signal_ac_sources": ["Vsa", "Vsb", "Vsc"],
            "phase_step_deg": 25.0,
            "pre_jump_s": 0.0, "post_jump_s": 0.0,
        })
        sines = [p for _, p in dut.calls if p.get("waveform") == "sine"]
        # 3 phases × 3 stages (pre / jump / restore) = 9 writes.
        assert len(sines) == 9
        # The middle stage's first phase write applied +25° offset.
        jump_phases = sorted(
            p["phase_deg"] for p in sines
            if p["phase_deg"] not in (0, 120, 240)
        )
        # +25, 145, 265 -- shifted versions of 0/120/240.
        assert jump_phases == [25.0, 145.0, 265.0]

    @pytest.mark.asyncio
    async def test_within_ieee_2800_band(self):
        tmpl = get_template("phase_jump")
        # ±25° is the standard mandate, ±30° our allowed margin.
        for deg in (-30.0, -25.0, 0.0, 25.0, 30.0):
            await tmpl.apply(_RecordingDUT(), {
                "signal_ac_sources": ["Vsa"],
                "phase_step_deg": deg,
                "pre_jump_s": 0.0, "post_jump_s": 0.0,
            })  # no exception

    @pytest.mark.asyncio
    async def test_beyond_30_degrees_raises(self):
        tmpl = get_template("phase_jump")
        with pytest.raises(ValueError, match="phase_step_deg"):
            await tmpl.apply(_RecordingDUT(), {
                "signal_ac_sources": ["Vsa"],
                "phase_step_deg": 45.0,
            })

    @pytest.mark.asyncio
    async def test_pref_optional_skipped_when_absent(self):
        tmpl = get_template("phase_jump")
        dut = _RecordingDUT()
        await tmpl.apply(dut, {
            "signal_ac_sources": ["Vsa"],
            "phase_step_deg": 10.0,
            "pre_jump_s": 0.0, "post_jump_s": 0.0,
            # no Pref_w -- P_ref must not be written
        })
        assert dut.writes_for("P_ref") == []


# ---------------------------------------------------------------------------
# frequency_deviation: missing-signal raise + IEEE 1547 band
# ---------------------------------------------------------------------------

class TestFrequencyDeviationEdges:
    @pytest.mark.asyncio
    async def test_no_signal_raises(self):
        tmpl = get_template("frequency_deviation")
        with pytest.raises(ValueError, match="signal"):
            await tmpl.apply(_RecordingDUT(), {
                "deviated_frequency_hz": 49.0,
            })

    @pytest.mark.asyncio
    async def test_out_of_band_raises(self):
        tmpl = get_template("frequency_deviation")
        with pytest.raises(ValueError, match="deviated_frequency_hz"):
            await tmpl.apply(_RecordingDUT(), {
                "signal": "Vgrid",
                "deviated_frequency_hz": 75.0,  # outside 40-65
            })


# ---------------------------------------------------------------------------
# _safe_sleep
# ---------------------------------------------------------------------------

class TestSafeSleep:
    @pytest.mark.asyncio
    async def test_caps_at_hard_max(self, monkeypatch):
        """``_safe_sleep`` must clamp the requested duration to
        ``hard_max`` so a typo (settle_s=600) doesn't hang the run."""
        slept_for: list[float] = []
        import asyncio
        async def _capture(seconds):
            slept_for.append(seconds)
        monkeypatch.setattr(asyncio, "sleep", _capture)

        await _safe_sleep(60.0, hard_max=2.0)
        assert slept_for == [2.0]

    @pytest.mark.asyncio
    async def test_passthrough_under_cap(self, monkeypatch):
        slept_for: list[float] = []
        import asyncio
        async def _capture(seconds):
            slept_for.append(seconds)
        monkeypatch.setattr(asyncio, "sleep", _capture)

        await _safe_sleep(0.5, hard_max=2.0)
        assert slept_for == [0.5]
