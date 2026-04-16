"""
pytest API introspection for the HTAF codegen pipeline.

Sibling of ``hil_api_docs.py``: both expose a parsed view of a reference API
that ``generate_tests`` can cite as context and ``validate_code`` can use to
cross-check the code it just produced.

Reference repository: https://github.com/pytest-dev/pytest

Instead of cloning that repo, we introspect the installed ``pytest`` module
at runtime. That gives us:

  * Every public top-level symbol (``pytest.fixture``, ``pytest.raises`` ...)
  * A hardcoded-but-tested list of built-in markers
    (``skip``, ``skipif``, ``xfail``, ``parametrize``, ``usefixtures`` ...)
  * Every ``pytest_*`` hook name defined in ``_pytest.hookspec``

The index loads lazily and is a no-op when pytest is not importable, so the
downstream nodes keep working on stripped-down mock environments.
"""

from __future__ import annotations

import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

logger = logging.getLogger(__name__)

# Built-in markers shipped with pytest. Kept as a fixed list because
# ``pytest.mark`` is a MarkGenerator proxy that accepts any attribute name
# (so ``dir(pytest.mark)`` is not authoritative).
_BUILTIN_MARKERS: tuple[str, ...] = (
    "skip",
    "skipif",
    "xfail",
    "parametrize",
    "usefixtures",
    "filterwarnings",
    "tryfirst",
    "trylast",
    "hookimpl",
    "no_cover",
)

# Reference repo — recorded in events/config for traceability.
PYTEST_REPO_URL = "https://github.com/pytest-dev/pytest/"


@dataclass
class PytestApiExecutor:
    """Singleton exposing an introspected view of the installed ``pytest``."""

    _public_api: dict[str, str] = field(default_factory=dict)    # name -> kind
    _hook_names: list[str] = field(default_factory=list)
    _version: str = ""
    _loaded: bool = False

    # ----- public API -----------------------------------------------------

    def load(self, force: bool = False) -> bool:
        """Introspect pytest. Returns True on success, False if not installed."""
        if self._loaded and not force:
            return True
        try:
            import pytest  # noqa: PLC0415 (runtime import is intentional)
        except ImportError:
            logger.info("pytest not available; skipping pytest API introspection")
            return False

        api: dict[str, str] = {}
        for name in dir(pytest):
            if name.startswith("_"):
                continue
            obj = getattr(pytest, name)
            if inspect.isclass(obj):
                kind = "class"
            elif inspect.isfunction(obj) or inspect.ismethod(obj):
                kind = "function"
            elif callable(obj):
                kind = "callable"
            else:
                kind = type(obj).__name__
            api[name] = kind

        hook_names: list[str] = []
        try:
            from _pytest import hookspec  # noqa: PLC0415

            hook_names = sorted(n for n in dir(hookspec) if n.startswith("pytest_"))
        except ImportError:
            hook_names = []

        self._public_api = api
        self._hook_names = hook_names
        self._version = getattr(pytest, "__version__", "")
        self._loaded = True
        logger.info(
            "Loaded pytest API index (v%s): %d public symbols, %d hooks, %d builtin markers",
            self._version,
            len(self._public_api),
            len(self._hook_names),
            len(_BUILTIN_MARKERS),
        )
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    def version(self) -> str:
        return self._version

    def list_public_api(self) -> list[str]:
        return sorted(self._public_api)

    def list_builtin_markers(self) -> list[str]:
        return list(_BUILTIN_MARKERS)

    def list_hook_names(self) -> list[str]:
        return list(self._hook_names)

    def has_attr(self, name: str) -> bool:
        return name in self._public_api

    def is_known_marker(
        self,
        name: str,
        *,
        extra: Iterable[str] = (),
    ) -> bool:
        if name in _BUILTIN_MARKERS:
            return True
        return name in set(extra)

    # ----- code inspection helpers ---------------------------------------

    def unknown_pytest_attrs(self, code: str) -> list[str]:
        """Return ``pytest.X`` attribute accesses whose X is not public pytest API.

        Ignores ``pytest.mark.*`` chains (those are handled by ``unknown_markers``)
        and returns an empty list when the API index is not loaded.
        """
        if not self._loaded:
            return []
        found = re.findall(r"\bpytest\.([A-Za-z_][A-Za-z0-9_]*)\b", code)
        unknown: list[str] = []
        seen: set[str] = set()
        for name in found:
            if name == "mark":
                # pytest.mark.* is a proxy that accepts anything; validated separately
                continue
            if name in seen:
                continue
            seen.add(name)
            if name not in self._public_api:
                unknown.append(name)
        return unknown

    def unknown_markers(
        self,
        code: str,
        *,
        declared: Iterable[str] = (),
    ) -> list[str]:
        """Return @pytest.mark.<name> markers not in builtins nor ``declared``.

        ``declared`` is the set of custom markers registered via pytest.ini's
        ``markers =`` section. Passing them here prevents false positives on
        project-defined markers like ``@pytest.mark.regulation``.
        """
        if not self._loaded:
            return []
        found = re.findall(r"@pytest\.mark\.([A-Za-z_][A-Za-z0-9_]*)\b", code)
        allowed = set(_BUILTIN_MARKERS) | set(declared)
        unknown: list[str] = []
        seen: set[str] = set()
        for name in found:
            if name in seen:
                continue
            seen.add(name)
            if name not in allowed:
                unknown.append(name)
        return unknown

    # ----- context header for generated files -----------------------------

    def summary_for_context(self, max_items: int = 20) -> str:
        """Compact ASCII-safe summary suitable for a comment-header injection."""
        if not self._loaded:
            return ""
        api = self.list_public_api()
        markers = ", ".join(_BUILTIN_MARKERS)
        hooks_count = len(self._hook_names)
        shown = api[:max_items]
        extra = len(api) - len(shown)
        lines = [
            f"# pytest API reference (v{self._version}; "
            f"source: {PYTEST_REPO_URL}):",
            f"#   builtin markers: {markers}",
            f"#   public symbols ({len(api)}): "
            + ", ".join(shown)
            + (f", ... (+{extra} more)" if extra > 0 else ""),
            f"#   plugin hookspec names available: {hooks_count}",
        ]
        header = "\n".join(lines)
        return "".join(ch for ch in header if ord(ch) < 128)


# ---------------------------------------------------------------------------
# Helpers used by validate_code to parse pytest.ini
# ---------------------------------------------------------------------------

def parse_ini_markers(ini_text: str) -> list[str]:
    """Extract custom marker names from a pytest.ini ``markers =`` block.

    pytest.ini syntax (simplified):
        [pytest]
        markers =
            regulation: Output regulation tests
            ripple: Ripple measurement tests

    Returns a list like ``["regulation", "ripple"]``. Unknown formats / missing
    sections yield an empty list (non-fatal).
    """
    if not ini_text:
        return []
    # Grab everything after "markers =" up to a blank line / next option / EOF.
    m = re.search(
        r"^\s*markers\s*=\s*(.*?)(?=^\s*[A-Za-z_][\w\- ]*\s*=|^\s*\[|\Z)",
        ini_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not m:
        return []
    body = m.group(1)
    markers: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # "regulation: desc" or just "regulation"
        name = line.split(":", 1)[0].strip()
        if name and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            markers.append(name)
    return markers


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_instance: PytestApiExecutor | None = None


def get_pytest_api(*, autoload: bool = True) -> PytestApiExecutor:
    """Return the shared PytestApiExecutor, loading on first access."""
    global _instance
    if _instance is None:
        _instance = PytestApiExecutor()
    if autoload and not _instance.is_loaded():
        _instance.load()
    return _instance
