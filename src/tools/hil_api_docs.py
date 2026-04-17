"""
HIL API documentation loader.

Parses the locally-installed Typhoon HIL Sphinx HTML docs (hil_api.html) and
exposes a simple index of available functions for the codegen pipeline.

Used by:
  - nodes/generate_tests.py  -> emit API-coverage context header + events
  - nodes/validate_code.py   -> cross-check hil.XXX(...) calls against the real API

The default path is controlled by configs/codegen.yaml or the THAA_HIL_API_DOC
environment variable; callers may also pass an explicit path.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default install paths Typhoon HIL Control Center writes on Windows
_DEFAULT_CANDIDATES = [
    r"C:/abc/Typhoon HIL Control Center 2026.1 sp1/Documentation/html/hil_api.html",
    r"C:/Program Files/Typhoon HIL Control Center 2026.1 sp1/Documentation/html/hil_api.html",
]

_MODULE_PREFIX = "typhoon.api.hil."


@dataclass
class HilApiFunction:
    """A single API member parsed from the Sphinx HTML docs."""

    name: str                                       # e.g. "load_model"
    qualname: str                                   # e.g. "typhoon.api.hil.load_model"
    signature: str = ""                             # "load_model(file, vhil_device=False)"
    description: str = ""                           # first paragraph of the doc entry
    kind: str = "function"                          # function | method | class | data


@dataclass
class HilApiDocsExecutor:
    """Singleton holding a parsed view of hil_api.html.

    Loading is lazy: the first call to ``load()`` parses the HTML; subsequent
    calls are no-ops unless a new path is given.
    """

    doc_path: str = ""
    _functions: dict[str, HilApiFunction] = field(default_factory=dict)
    _loaded_path: str | None = None

    # ----- public API -----------------------------------------------------

    def resolve_path(self, path: str | None = None, *, strict: bool = False) -> str:
        """Pick the doc path.

        When ``strict`` is True, only the explicit ``path`` argument is considered
        - no environment / config / default fallback. This is what callers that
        mean a specific file (e.g. unit tests) want.
        """
        if strict:
            return path if path and Path(path).is_file() else ""

        candidates: list[str] = []
        if path:
            candidates.append(path)
        env = os.environ.get("THAA_HIL_API_DOC")
        if env:
            candidates.append(env)
        if self.doc_path:
            candidates.append(self.doc_path)
        candidates.extend(_DEFAULT_CANDIDATES)

        for c in candidates:
            if c and Path(c).is_file():
                return c
        return ""

    def load(self, path: str | None = None, force: bool = False) -> bool:
        """Parse hil_api.html. Returns True on success, False if no doc found.

        An explicit ``path`` argument is treated as an exact request: if it does
        not exist we do NOT silently fall back to system defaults.
        """
        resolved = self.resolve_path(path, strict=bool(path))
        if not resolved:
            logger.info("HIL API docs not found (searched default locations)")
            return False
        if not force and self._loaded_path == resolved and self._functions:
            return True

        try:
            html = Path(resolved).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("Could not read %s: %s", resolved, exc)
            return False

        self._functions = self._parse(html)
        self._loaded_path = resolved
        logger.info("Loaded %d HIL API members from %s", len(self._functions), resolved)
        return True

    def is_loaded(self) -> bool:
        return bool(self._functions)

    def list_names(self) -> list[str]:
        return sorted(self._functions.keys())

    def count(self) -> int:
        return len(self._functions)

    def has(self, name: str) -> bool:
        """Check a bare name (without the 'typhoon.api.hil.' prefix)."""
        return name in self._functions

    def get(self, name: str) -> HilApiFunction | None:
        return self._functions.get(name)

    def summary_for_context(self, max_items: int = 30) -> str:
        """Compact ASCII-safe summary suitable for embedding in a comment header.

        The output is injected into generated .py files; CLAUDE.md mandates pure
        ASCII so we defensively drop any non-ASCII char that the parser missed.
        """
        names = self.list_names()
        if not names:
            return ""
        shown = names[:max_items]
        extra = len(names) - len(shown)
        lines = [f"# HIL API reference ({len(names)} members loaded from hil_api.html):"]
        for n in shown:
            fn = self._functions[n]
            sig = fn.signature or f"{n}(...)"
            lines.append(f"#   hil.{sig}")
        if extra > 0:
            lines.append(f"#   ... and {extra} more")
        header = "\n".join(lines)
        return "".join(ch for ch in header if ord(ch) < 128)

    def unknown_calls(self, code: str) -> list[str]:
        """Return hil.<name>(...) tokens in *code* that are NOT in the API index.

        Returns an empty list when the index is not loaded (non-blocking fallback).
        """
        if not self._functions:
            return []
        found = re.findall(r"\bhil\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", code)
        unknown = []
        seen: set[str] = set()
        for name in found:
            if name in seen:
                continue
            seen.add(name)
            if name not in self._functions:
                unknown.append(name)
        return unknown

    # ----- parser ---------------------------------------------------------

    def _parse(self, html: str) -> dict[str, HilApiFunction]:
        """Parse Sphinx-generated hil_api.html into HilApiFunction records.

        Strategy:
          1. Prefer BeautifulSoup when available (accurate extraction from
             <dl class="py function"> / <dt id="typhoon.api.hil.X"> blocks).
          2. Fall back to a regex over the raw HTML that only captures the
             qualnames (signatures left blank). Good enough for validation.
        """
        try:
            return self._parse_bs4(html)
        except ImportError:
            logger.info("bs4 not available, falling back to regex parser")
            return self._parse_regex(html)
        except Exception as exc:
            logger.warning("bs4 parse failed (%s), falling back to regex", exc)
            return self._parse_regex(html)

    def _parse_bs4(self, html: str) -> dict[str, HilApiFunction]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        out: dict[str, HilApiFunction] = {}

        # Sphinx emits one <dt id="typhoon.api.hil.XXX"> per documented member.
        for dt in soup.find_all("dt"):
            qualname = dt.get("id", "")
            if not qualname.startswith(_MODULE_PREFIX):
                continue
            bare = qualname[len(_MODULE_PREFIX):]
            if not bare or "." in bare:
                # skip nested / class attribute ids
                continue

            # Try to reconstruct the signature: concatenate visible text of the
            # <dt>, strip the trailing "[source]" link, remove Sphinx headerlink
            # glyphs (paragraph pilcrow + non-ASCII), and normalize whitespace.
            sig_text = dt.get_text(" ", strip=True)
            sig_text = re.sub(r"\s*\[source\]\s*$", "", sig_text)
            # Drop any non-ASCII char so the result can be safely embedded into
            # generated .py files that must stay pure ASCII (see CLAUDE.md).
            sig_text = "".join(ch for ch in sig_text if ord(ch) < 128)
            sig_text = re.sub(r"\s+", " ", sig_text).strip()
            if sig_text.startswith("typhoon.api.hil."):
                sig_text = sig_text[len("typhoon.api.hil."):]

            # First paragraph of the sibling <dd> is the doc description.
            description = ""
            dd = dt.find_next_sibling("dd")
            if dd is not None:
                first_p = dd.find("p")
                if first_p is not None:
                    description = re.sub(r"\s+", " ", first_p.get_text(" ", strip=True))

            kind = "function"
            parent_dl = dt.find_parent("dl")
            if parent_dl is not None:
                classes = parent_dl.get("class") or []
                for c in ("method", "class", "data", "attribute", "function"):
                    if c in classes:
                        kind = c
                        break

            out[bare] = HilApiFunction(
                name=bare,
                qualname=qualname,
                signature=sig_text,
                description=description,
                kind=kind,
            )
        return out

    def _parse_regex(self, html: str) -> dict[str, HilApiFunction]:
        out: dict[str, HilApiFunction] = {}
        for m in re.finditer(r'id="(typhoon\.api\.hil\.[^"]+)"', html):
            qualname = m.group(1)
            bare = qualname[len(_MODULE_PREFIX):]
            if not bare or "." in bare or bare in out:
                continue
            out[bare] = HilApiFunction(name=bare, qualname=qualname)
        return out


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_instance: HilApiDocsExecutor | None = None


def get_hil_api_docs(
    path: str | None = None,
    *,
    autoload: bool = True,
) -> HilApiDocsExecutor:
    """Return the shared HilApiDocsExecutor, optionally loading docs on first access."""
    global _instance
    if _instance is None:
        _instance = HilApiDocsExecutor(doc_path=path or "")
    if autoload and not _instance.is_loaded():
        _instance.load(path)
    elif path and _instance._loaded_path not in {None, _instance.resolve_path(path)}:
        _instance.load(path, force=True)
    return _instance
