"""Hard Rule 3.1 — Python source MUST be ASCII.

Windows TyphoonTest IDE reads .py files as cp1254 and crashes on
multi-byte characters. This test scans every shipped source file
under ``src/`` and ``scripts/`` and fails fast if any non-ASCII
character is committed.

Allow-listed: ``prompts/`` (markdown, ok to be Korean), ``configs/``
(YAML descriptions ok), ``docs/`` (markdown), ``tests/`` (test code
that intentionally exercises encoding paths).

If the test fails, run::

    python -c "import pathlib; \\
        [print(p, e.start) for p in pathlib.Path('src').rglob('*.py') \\
         for e in [None] \\
         if (e := _try_decode(p))]"

then strip the offending characters or move the comment to a sibling
``.md`` file.
"""

from __future__ import annotations

from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent
_GUARDED = ("src", "scripts")


def _python_files():
    for top in _GUARDED:
        for p in (_ROOT / top).rglob("*.py"):
            # ``__pycache__`` and friends never have source we author.
            if "__pycache__" in p.parts:
                continue
            yield p


@pytest.mark.parametrize("path", list(_python_files()),
                          ids=lambda p: str(p.relative_to(_ROOT)))
def test_source_is_ascii(path: Path) -> None:
    """Every byte in src/scripts must be ASCII (< 0x80)."""
    raw = path.read_bytes()
    try:
        raw.decode("ascii")
    except UnicodeDecodeError as exc:
        # Locate the line so the failure message points at it.
        before = raw[: exc.start]
        line = before.count(b"\n") + 1
        col = len(before) - before.rfind(b"\n") - 1
        snippet = raw[max(0, exc.start - 20): exc.start + 20]
        pytest.fail(
            f"Non-ASCII byte at {path.relative_to(_ROOT)}:{line}:{col}: "
            f"0x{exc.object[exc.start]:02x}. Snippet: {snippet!r}. "
            f"Hard Rule 3.1: move the comment to a .md file or strip "
            f"the character (TyphoonTest IDE crashes on cp1254 input).",
        )
