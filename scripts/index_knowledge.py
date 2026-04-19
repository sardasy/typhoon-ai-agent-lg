"""
Knowledge-base indexer for THAA RAG.

Scans repo + IEEE skill references + past reports and builds an embedded
ChromaDB collection set under ``chroma_db/``. Re-runnable: existing
collections are wiped and rebuilt.

Sources indexed:
  thaa_standards    : IEEE 1547 / 2800 reference markdown (from skill)
                      + extracts from scenarios.yaml standard_ref fields
  thaa_api_docs     : src/tools/*.py docstrings + Typhoon API hints
  thaa_test_history : reports/*.html (rendered scenario results)
  thaa_scenarios    : configs/scenarios_*.yaml (each scenario as one doc)

Usage:
  python scripts/index_knowledge.py             # index everything
  python scripts/index_knowledge.py --source standards
  python scripts/index_knowledge.py --clean     # wipe + reindex
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import yaml

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("indexer")

ROOT = Path(__file__).resolve().parent.parent
CHROMA_DIR = ROOT / "chroma_db"
COLLECTION_PREFIX = "thaa"

# Heuristic search paths for the IEEE reference markdown bundled in the
# typhoon-hil-rag-pipeline plugin skill.
IEEE_REF_CANDIDATES = [
    Path.home() / "AppData/Roaming/Claude/local-agent-mode-sessions",
    Path.home() / ".claude/local-agent-mode-sessions",
]


def _find_ieee_refs() -> list[Path]:
    """Locate ieee-standards-guide reference markdown files."""
    out: list[Path] = []
    for root in IEEE_REF_CANDIDATES:
        if not root.exists():
            continue
        for p in root.rglob("ieee-standards-guide/references/*.md"):
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# Document collectors
# ---------------------------------------------------------------------------

def collect_standards() -> list[dict]:
    docs: list[dict] = []

    for ref in _find_ieee_refs():
        text = ref.read_text(encoding="utf-8")
        # Split by ## headings into chunks
        sections = re.split(r"\n## ", text)
        for i, sec in enumerate(sections):
            sec = sec.strip()
            if len(sec) < 100:
                continue
            doc_id = f"ieee_{ref.stem}_{i}"
            docs.append({
                "id": doc_id,
                "text": sec[:4000],
                "metadata": {
                    "standard": "IEEE 1547",
                    "topic": ref.stem,
                    "source_path": str(ref.name),
                },
            })

    # Inline IEEE 2800 / IEC summaries (compact knowledge from this session)
    docs.extend([
        {
            "id": "ieee2800_voltage_source",
            "text": (
                "IEEE 2800-2022 Section 9 (Voltage Source Behavior of GFM IBR). "
                "A grid-forming inverter shall behave as a controllable voltage "
                "source behind defined impedance. Internal voltage magnitude must "
                "track Vref under steady state; phase angle controllable via VSM "
                "swing equation; voltage maintained during grid impedance step "
                "(no collapse). Acceptable RMS tolerance 5% at zero export."
            ),
            "metadata": {"standard": "IEEE 2800-2022", "section": "9"},
        },
        {
            "id": "ieee2800_virtual_inertia",
            "text": (
                "IEEE 2800-2022 Section 7.2.2 (Synthetic / Virtual Inertia). "
                "GFM IBR shall produce inertia-like response to grid frequency "
                "disturbances: synthetic inertia constant H 5-10s; ROCOF response "
                "bounded; settling time after Pref step <= 5s. Tunable via VSM "
                "swing equation: J (moment of inertia), D (damping), Kv (voltage "
                "droop). Higher J yields slower but more stable response."
            ),
            "metadata": {"standard": "IEEE 2800-2022", "section": "7.2.2"},
        },
        {
            "id": "ieee2800_ffci",
            "text": (
                "IEEE 2800-2022 Section 7.4 (Fast Fault Current Injection). "
                "GFM IBR must inject reactive current within ~1 cycle (<=20ms at "
                "50Hz) of detecting voltage sag, sustain 1.0-1.5 pu reactive "
                "current for fault duration (typ 300ms), and not exceed 1.5 pu "
                "peak. Triggered by voltage drop below 0.5 pu."
            ),
            "metadata": {"standard": "IEEE 2800-2022", "section": "7.4"},
        },
        {
            "id": "ieee2800_phase_jump",
            "text": (
                "IEEE 2800-2022 Section 7.3 (Phase Jump Response). GFM IBR must "
                "remain stable through grid phase angle steps up to +/- 25 deg. "
                "Inverter currents must stay <= 1.5 pu peak; resync within 1.0s. "
                "VSM tuning J, D affects resync speed."
            ),
            "metadata": {"standard": "IEEE 2800-2022", "section": "7.3"},
        },
        {
            "id": "iec62619_overvoltage",
            "text": (
                "IEC 62619 7.2.1: Each cell shall be individually monitored for "
                "overvoltage. Protection shall activate within 100ms of detection. "
                "System disconnects battery from load when any cell exceeds max V."
            ),
            "metadata": {"standard": "IEC 62619", "section": "7.2.1"},
        },
        {
            "id": "iec62619_undervoltage",
            "text": (
                "IEC 62619 7.2.2: Undervoltage protection shall prevent cell "
                "voltage from dropping below minimum. Response within 200ms."
            ),
            "metadata": {"standard": "IEC 62619", "section": "7.2.2"},
        },
        {
            "id": "ul9540_thermal",
            "text": (
                "UL 9540 Section 38 (ESS Thermal Protection). Power module "
                "thermal monitoring required (junction max ~125 C). Heatsink "
                "limit typically 85 C. On overtemperature, controlled shutdown "
                "preferred (gradual power reduction). Clearing time <= 1.0s."
            ),
            "metadata": {"standard": "UL 9540", "section": "38"},
        },
        {
            "id": "iec61851_rcd",
            "text": (
                "IEC 62955 / IEC 61851-1 Section 6.3.3: Type A RCD must trip "
                "at 5 mA residual current, clearing time <= 40 ms. All AC "
                "contactors must open on RCD trip."
            ),
            "metadata": {"standard": "IEC 61851-1", "section": "6.3.3"},
        },
    ])

    return docs


def collect_scenarios() -> list[dict]:
    docs: list[dict] = []
    for yml in (ROOT / "configs").glob("scenarios*.yaml"):
        try:
            data = yaml.safe_load(yml.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            log.warning("Skipping %s: %s", yml.name, exc)
            continue
        scenarios = data.get("scenarios") or {}
        for sid, spec in scenarios.items():
            if not isinstance(spec, dict):
                continue
            text = (
                f"[{spec.get('category','?')}] {sid}\n"
                f"Description: {spec.get('description','')}\n"
                f"Standard: {spec.get('standard_ref','')}\n"
                f"Parameters: {spec.get('parameters', {})}\n"
                f"Pass/Fail rules: {spec.get('pass_fail_rules', {})}\n"
            )
            docs.append({
                "id": f"scen_{yml.stem}_{sid}",
                "text": text[:3000],
                "metadata": {
                    "yaml_file": yml.name,
                    "category": str(spec.get("category", "")),
                    "standard": str(spec.get("standard_ref", "")),
                },
            })
    return docs


def collect_api_docs() -> list[dict]:
    """Pull docstrings out of src/tools/*.py for API help."""
    docs: list[dict] = []
    for py in (ROOT / "src" / "tools").glob("*.py"):
        if py.name.startswith("__"):
            continue
        text = py.read_text(encoding="utf-8")
        # Module-level docstring
        m = re.match(r'^"""(.*?)"""', text, re.DOTALL)
        if m:
            docs.append({
                "id": f"api_{py.stem}_module",
                "text": m.group(1).strip()[:2000],
                "metadata": {"module": py.stem, "kind": "module_docstring"},
            })
        # Per-function/class docstrings
        seen_ids: dict[str, int] = {}
        for fn in re.finditer(
            r'(?:async\s+def|def|class)\s+(\w+)[^\n]*:\s*\n\s*"""(.*?)"""',
            text, re.DOTALL,
        ):
            name, doc = fn.group(1), fn.group(2).strip()
            if len(doc) < 30:
                continue
            base = f"api_{py.stem}_{name}"
            count = seen_ids.get(base, 0)
            seen_ids[base] = count + 1
            doc_id = base if count == 0 else f"{base}_{count}"
            docs.append({
                "id": doc_id,
                "text": f"{py.stem}.{name}: {doc}"[:2000],
                "metadata": {"module": py.stem, "symbol": name},
            })
    return docs


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def collect_test_history() -> list[dict]:
    docs: list[dict] = []
    for html in (ROOT / "reports").glob("report_*.html"):
        text = html.read_text(encoding="utf-8", errors="ignore")
        ext = _HTMLTextExtractor()
        try:
            ext.feed(text)
        except Exception:
            continue
        body = "\n".join(ext.parts)
        if len(body) < 100:
            continue
        docs.append({
            "id": f"hist_{html.stem}",
            "text": body[:4000],
            "metadata": {"report_file": html.name, "kind": "test_report"},
        })
    return docs


# ---------------------------------------------------------------------------
# ChromaDB writer
# ---------------------------------------------------------------------------

def _client():
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )


def upsert_collection(name: str, docs: list[dict], clean: bool = True) -> int:
    if not docs:
        log.info("[%s] no documents", name)
        return 0
    client = _client()
    full_name = f"{COLLECTION_PREFIX}_{name}"
    if clean:
        try:
            client.delete_collection(name=full_name)
        except Exception:
            pass
    col = client.get_or_create_collection(name=full_name)
    ids = [d["id"] for d in docs]
    texts = [d["text"] for d in docs]
    metas = [
        {k: (v if isinstance(v, (str, int, float, bool)) else str(v))
         for k, v in d.get("metadata", {}).items()} or {"_": "_"}
        for d in docs
    ]
    col.add(ids=ids, documents=texts, metadatas=metas)
    log.info("[%s] indexed %d documents (collection=%s)", name, len(docs), full_name)
    return len(docs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

SOURCES = {
    "standards": collect_standards,
    "scenarios": collect_scenarios,
    "api_docs": collect_api_docs,
    "test_history": collect_test_history,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", choices=list(SOURCES) + ["all"], default="all")
    ap.add_argument("--clean", action="store_true",
                    help="Wipe each collection before reindexing (default behaviour)")
    ap.add_argument("--no-clean", dest="clean", action="store_false",
                    help="Append to existing collection instead of wiping")
    ap.set_defaults(clean=True)
    args = ap.parse_args(argv)

    sources = list(SOURCES) if args.source == "all" else [args.source]
    total = 0
    for src in sources:
        docs = SOURCES[src]()
        log.info("collected %d docs for '%s'", len(docs), src)
        total += upsert_collection(src, docs, clean=args.clean)
    log.info("DONE — %d documents indexed under %s", total, CHROMA_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
