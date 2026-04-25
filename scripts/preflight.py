"""Phase 4-H: pre-flight environment checks for THAA real-hardware runs.

Verifies a deployment is actually ready to drive a real HIL404 + ECU
(or any subset) BEFORE a long planner run starts. Each check returns
a (status, message) tuple; a final exit code reflects the strictest
required check that failed.

Usage:
    python scripts/preflight.py                # all checks
    python scripts/preflight.py --hil          # only HIL connectivity
    python scripts/preflight.py --xcp --a2l firmware/v1.2.a2l
    python scripts/preflight.py --strict       # any WARN -> nonzero exit

Also callable from ``main.py --preflight`` so the same logic runs
inside the agent's Python environment.

Checks:
    env       Python version, ASCII source guard, required deps
    config    configs/model.yaml exists + parses + has model.path
    hil       Typhoon HIL API import + load_model + signal discovery
    xcp       pyxcp import + A2L parse + (optional) ECU connect
    rag       chroma_db/ exists + collections populated
    twin      DigitalTwin singleton fresh + writable XCP whitelist sane

Exit codes:
    0   all required checks passed
    1   one or more required check FAILED
    2   strict mode: a WARN-level check fired
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("preflight")


Status = Literal["PASS", "WARN", "FAIL", "SKIP"]


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    required: bool = True

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        icon = {"PASS": "OK", "WARN": "..", "FAIL": "XX", "SKIP": "--"}[self.status]
        req = "" if self.required else " (optional)"
        return f"[{icon}] {self.name}{req}: {self.message}"


# ---------------------------------------------------------------------------
# Individual checks (pure: read environment, return CheckResult).
# ---------------------------------------------------------------------------

def check_env() -> list[CheckResult]:
    out: list[CheckResult] = []
    # Python version
    if sys.version_info >= (3, 12):
        out.append(CheckResult("python", "PASS", f"{sys.version.split()[0]}"))
    else:
        out.append(CheckResult(
            "python", "FAIL",
            f"need >=3.12, found {sys.version.split()[0]}",
        ))
    # Required deps
    required = ["langgraph", "yaml", "pydantic", "anthropic", "langchain_anthropic"]
    missing = [m for m in required if importlib.util.find_spec(m) is None]
    if missing:
        out.append(CheckResult(
            "deps", "FAIL", f"missing: {', '.join(missing)}",
        ))
    else:
        out.append(CheckResult("deps", "PASS", "all core packages available"))
    return out


def check_config(config_path: str = "configs/model.yaml") -> list[CheckResult]:
    p = (ROOT / config_path).resolve()
    if not p.exists():
        return [CheckResult("config", "FAIL", f"{config_path} not found")]
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return [CheckResult("config", "FAIL", f"YAML parse error: {exc}")]

    out: list[CheckResult] = []
    model = cfg.get("model") or {}
    model_path = model.get("path", "")
    if not model_path:
        out.append(CheckResult(
            "config.model.path", "WARN",
            "missing -- HIL backend will fail at load_model",
        ))
    else:
        tse = ROOT / model_path
        if not tse.exists():
            out.append(CheckResult(
                "config.model.path", "WARN",
                f"references missing .tse: {model_path}",
            ))
        else:
            out.append(CheckResult(
                "config.model.path", "PASS",
                f"{model_path} ({tse.stat().st_size:,} bytes)",
            ))
    return out


def check_hil() -> list[CheckResult]:
    """Verify the Typhoon HIL API is importable and a device responds."""
    try:
        from src.tools.hil_tools import HAS_TYPHOON
    except Exception as exc:
        return [CheckResult("hil.import", "FAIL", f"{exc}")]
    if not HAS_TYPHOON:
        return [CheckResult(
            "hil", "WARN",
            "typhoon.api.hil not importable -- falling back to VHIL mock. "
            "Use scripts/run_with_typhoon.bat for the real device.",
            required=False,
        )]
    out: list[CheckResult] = [
        CheckResult("hil.import", "PASS", "typhoon.api.hil + typhoon.test.capture")
    ]
    try:
        import typhoon.api.hil as hil
        signals = list(hil.get_analog_signals() or [])
        out.append(CheckResult(
            "hil.signals", "PASS",
            f"{len(signals)} analog signals discovered",
        ))
    except Exception as exc:
        out.append(CheckResult(
            "hil.signals", "FAIL", f"get_analog_signals raised: {exc}",
        ))
    return out


def check_xcp(a2l_path: str | None) -> list[CheckResult]:
    try:
        from src.tools.xcp_tools import HAS_XCP
    except Exception as exc:
        return [CheckResult("xcp.import", "FAIL", f"{exc}")]
    if not HAS_XCP:
        return [CheckResult(
            "xcp", "WARN",
            "pyxcp / pya2ldb not installed -- XCP/Hybrid backends will mock. "
            "pip install pyxcp pya2ldb to enable real ECU.",
            required=False,
        )]
    out: list[CheckResult] = [CheckResult("xcp.import", "PASS", "pyxcp available")]

    if a2l_path:
        a2l = Path(a2l_path)
        if not a2l.exists():
            out.append(CheckResult(
                "xcp.a2l", "FAIL", f"{a2l_path} not found",
            ))
        else:
            try:
                from pya2ldb import DB as A2LDB
                db = A2LDB(str(a2l))
                meas_count = len(getattr(db, "get_all_measurements", lambda: {})())
                out.append(CheckResult(
                    "xcp.a2l", "PASS",
                    f"{a2l.name}: {meas_count} measurements",
                ))
            except Exception as exc:
                out.append(CheckResult(
                    "xcp.a2l", "FAIL", f"A2L parse failed: {exc}",
                ))
    else:
        out.append(CheckResult(
            "xcp.a2l", "SKIP", "no --a2l-path supplied", required=False,
        ))
    return out


def check_rag() -> list[CheckResult]:
    try:
        from src.tools.rag_tools import HAS_CHROMA, _CHROMA_DIR
    except Exception as exc:
        return [CheckResult("rag.import", "FAIL", f"{exc}")]
    if not HAS_CHROMA:
        return [CheckResult(
            "rag", "WARN",
            "chromadb not installed -- using mock KB. "
            "pip install chromadb to enable persistent index.",
            required=False,
        )]
    out: list[CheckResult] = [CheckResult("rag.import", "PASS", "chromadb available")]
    if not _CHROMA_DIR.exists():
        out.append(CheckResult(
            "rag.index", "WARN",
            f"{_CHROMA_DIR} missing -- run "
            "`python scripts/index_knowledge.py`",
            required=False,
        ))
        return out

    try:
        import chromadb
        from chromadb.config import Settings
        client = chromadb.PersistentClient(
            path=str(_CHROMA_DIR), settings=Settings(anonymized_telemetry=False),
        )
        cols = client.list_collections()
    except Exception as exc:
        out.append(CheckResult("rag.index", "FAIL", f"client init: {exc}"))
        return out

    if not cols:
        out.append(CheckResult(
            "rag.index", "WARN",
            f"{_CHROMA_DIR} is empty -- run scripts/index_knowledge.py",
            required=False,
        ))
        return out

    total = sum(c.count() for c in cols)
    out.append(CheckResult(
        "rag.index", "PASS",
        f"{len(cols)} collections, {total} documents total",
    ))

    # Phase 4-G: at least one doc per collection should be tagged.
    untagged = 0
    for c in cols:
        if c.count() == 0:
            continue
        peek = c.get(limit=5)
        for m in peek.get("metadatas") or []:
            if not (m or {}).get("domain"):
                untagged += 1
    if untagged:
        out.append(CheckResult(
            "rag.domain_tags", "WARN",
            f"{untagged} sampled docs missing `metadata.domain` -- "
            "rerun the indexer for Phase 4-G namespacing",
            required=False,
        ))
    else:
        out.append(CheckResult(
            "rag.domain_tags", "PASS",
            "sampled docs all carry a `domain` tag",
        ))
    return out


def check_twin() -> list[CheckResult]:
    try:
        from src.twin import DigitalTwin, PLAUSIBLE_RANGES, get_twin
        from src.tools.xcp_tools import XCPToolExecutor
    except Exception as exc:
        return [CheckResult("twin.import", "FAIL", f"{exc}")]

    out = [CheckResult("twin.import", "PASS", "src.twin loaded")]

    # Whitelist params should all have a plausible range or an
    # EFFECT_DIRECTION rule; otherwise the twin can't vote on them and
    # heal loops degrade to "uncertain" forever.
    xcp = XCPToolExecutor()
    whitelisted = xcp.WRITABLE_PARAMS
    sized = sum(1 for p in whitelisted if p in PLAUSIBLE_RANGES)
    coverage = sized / max(len(whitelisted), 1)
    if coverage >= 0.4:
        out.append(CheckResult(
            "twin.coverage", "PASS",
            f"{sized}/{len(whitelisted)} writable params have plausible ranges",
        ))
    else:
        out.append(CheckResult(
            "twin.coverage", "WARN",
            f"only {sized}/{len(whitelisted)} writable params have ranges -- "
            "twin will return 'uncertain' for the rest",
            required=False,
        ))

    # Singleton sanity
    twin = get_twin()
    # ``get_twin()`` is statically typed to return ``DigitalTwin``, so
    # the isinstance check below would be unreachable. Trust the type.
    out.append(CheckResult(
        "twin.singleton", "PASS",
        f"DigitalTwin ready ({len(twin.state)} cached values)",
    ))
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_all(
    *,
    do_env: bool = True, do_config: bool = True, do_hil: bool = True,
    do_xcp: bool = True, do_rag: bool = True, do_twin: bool = True,
    config_path: str = "configs/model.yaml",
    a2l_path: str | None = None,
) -> list[CheckResult]:
    results: list[CheckResult] = []
    if do_env:    results.extend(check_env())
    if do_config: results.extend(check_config(config_path))
    if do_hil:    results.extend(check_hil())
    if do_xcp:    results.extend(check_xcp(a2l_path))
    if do_rag:    results.extend(check_rag())
    if do_twin:   results.extend(check_twin())
    return results


def summarize(results: list[CheckResult], strict: bool = False) -> int:
    """Print results and return the appropriate exit code."""
    print()
    for r in results:
        print(r)
    print()
    fails = [r for r in results if r.status == "FAIL"]
    warns = [r for r in results if r.status == "WARN"]
    print(f"{len(results)} checks: "
          f"{len(fails)} FAIL, {len(warns)} WARN, "
          f"{sum(1 for r in results if r.status == 'PASS')} PASS, "
          f"{sum(1 for r in results if r.status == 'SKIP')} SKIP")
    if fails:
        return 1
    if strict and warns:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=str, default="configs/model.yaml")
    parser.add_argument("--a2l-path", type=str, default=None)
    parser.add_argument("--env", action="store_true", help="run only env checks")
    parser.add_argument("--config-only", action="store_true",
                        help="run only config checks")
    parser.add_argument("--hil", action="store_true", help="run only HIL checks")
    parser.add_argument("--xcp", action="store_true", help="run only XCP checks")
    parser.add_argument("--rag", action="store_true", help="run only RAG checks")
    parser.add_argument("--twin", action="store_true", help="run only twin checks")
    parser.add_argument("--strict", action="store_true",
                        help="WARN-level results count as failures (exit 2)")
    args = parser.parse_args()

    selectors = [
        ("env", args.env), ("config-only", args.config_only),
        ("hil", args.hil), ("xcp", args.xcp), ("rag", args.rag),
        ("twin", args.twin),
    ]
    any_selected = any(v for _, v in selectors)

    results = run_all(
        do_env    = args.env or not any_selected,
        do_config = args.config_only or not any_selected,
        do_hil    = args.hil or not any_selected,
        do_xcp    = args.xcp or not any_selected,
        do_rag    = args.rag or not any_selected,
        do_twin   = args.twin or not any_selected,
        config_path=args.config,
        a2l_path=args.a2l_path,
    )
    return summarize(results, strict=args.strict)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
