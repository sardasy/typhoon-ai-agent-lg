"""Hard Rule 3.2 -- safe pd.Timedelta indexing helpers for capture results.

``typhoon.test.capture`` returns a DataFrame whose index is
``pd.Timedelta``. Integer / float ``.iloc[100]`` / ``.loc[0.001]`` calls
silently return wrong rows or raise unexpected KeyError on subsequent
runs. These helpers wrap the correct pattern so test authors never
need to remember the rule.

Usage::

    from src.timedelta_helpers import at, between

    df = capture.get_capture_results()
    v_at_1ms = at(df, 0.001, signal="Vout")
    sag_window = between(df, 0.1, 0.5, signal="Vac")
"""

from __future__ import annotations

from typing import Any


def _td(seconds: float) -> Any:
    """Build a ``pd.Timedelta`` from seconds. Imported lazily so this
    module can be imported even when pandas isn't installed (Mock
    DUT runs)."""
    import pandas as pd
    return pd.Timedelta(seconds=float(seconds))


def at(df: Any, t_seconds: float, *, signal: str | None = None) -> Any:
    """Return the row (or scalar) at ``t_seconds``.

    Examples::

        at(df, 0.001)            # full row at 1 ms
        at(df, 0.001, signal="Vout")  # scalar value
    """
    row = df.loc[_td(t_seconds)]
    if signal is None:
        return row
    return row[signal]


def between(
    df: Any,
    start_s: float,
    stop_s: float,
    *,
    signal: str | None = None,
) -> Any:
    """Return the slice between ``start_s`` and ``stop_s`` (inclusive).

    Slicing a Timedelta-indexed frame requires both endpoints to also
    be Timedeltas; this helper wraps that conversion.
    """
    sl = df.loc[_td(start_s):_td(stop_s)]
    if signal is None:
        return sl
    return sl[signal]


def assert_at(
    df: Any, t_seconds: float, *, signal: str,
    expected: float, tolerance: float = 0.0,
) -> None:
    """Hard assertion used inside pytest tests."""
    actual = at(df, t_seconds, signal=signal)
    if abs(float(actual) - expected) > tolerance:
        raise AssertionError(
            f"{signal} at t={t_seconds}s expected {expected} "
            f"+/-{tolerance}, got {actual}",
        )
