"""Disk lifecycle cleanup for THAA artifacts.

THAA accumulates several output directories during normal operation:

    reports/        Jinja2 HTML reports, one per ``main.py --goal`` run
    runs/           HITL audit JSONL, SQLite checkpoint DBs
    output/         HTAF codegen artifacts (zip bundles)
    uploads/        Frontend .tse uploads
    chroma_db/      RAG vector index (rebuilt by index_knowledge.py)

This script trims them under operator-defined retention policies.
Defaults are conservative (keep last 30 reports, 90-day audit log,
60-day codegen output, 30-day uploads, never touch chroma_db/).

Usage:
    python scripts/cleanup.py                       # dry-run
    python scripts/cleanup.py --apply               # actually delete
    python scripts/cleanup.py --apply --reports 10  # custom retention
    python scripts/cleanup.py --apply --all-old --days 7
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("cleanup")

ROOT = Path(__file__).resolve().parent.parent

REPORT_PATTERN = re.compile(r"^report_\d{8}_\d{6}\.html$")


@dataclass
class CleanupPlan:
    """What we'd delete. Built by :func:`plan` and applied by :func:`apply`."""
    reports_to_delete: list[Path]
    runs_to_delete: list[Path]
    output_to_delete: list[Path]
    uploads_to_delete: list[Path]

    @property
    def total_count(self) -> int:
        return (
            len(self.reports_to_delete) + len(self.runs_to_delete)
            + len(self.output_to_delete) + len(self.uploads_to_delete)
        )

    @property
    def total_bytes(self) -> int:
        all_paths = (
            self.reports_to_delete + self.runs_to_delete
            + self.output_to_delete + self.uploads_to_delete
        )
        return sum(p.stat().st_size for p in all_paths if p.is_file())


def _older_than(path: Path, days: int) -> bool:
    if not path.is_file():
        return False
    age = _dt.datetime.now(_dt.timezone.utc).timestamp() - path.stat().st_mtime
    return age > days * 86400


def _newest_first(directory: Path, pattern: re.Pattern[str] | None = None) -> list[Path]:
    if not directory.exists():
        return []
    files = []
    for p in directory.iterdir():
        if not p.is_file():
            continue
        if pattern is not None and not pattern.match(p.name):
            continue
        files.append(p)
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def plan(
    *,
    keep_reports: int = 30,
    runs_days: int = 90,
    output_days: int = 60,
    uploads_days: int = 30,
) -> CleanupPlan:
    """Build a cleanup plan without deleting anything."""
    # Reports: keep N newest matching report_YYYYMMDD_HHMMSS.html
    reports_dir = ROOT / "reports"
    all_reports = _newest_first(reports_dir, REPORT_PATTERN)
    reports_to_delete = all_reports[keep_reports:]

    # Runs: by mtime age (HITL audit + SQLite checkpoint DBs).
    runs_dir = ROOT / "runs"
    runs_to_delete: list[Path] = []
    if runs_dir.exists():
        for p in runs_dir.iterdir():
            if _older_than(p, runs_days):
                runs_to_delete.append(p)

    # Output (codegen zips)
    output_dir = ROOT / "output" / "generated_tests"
    output_to_delete: list[Path] = []
    if output_dir.exists():
        for p in output_dir.iterdir():
            if _older_than(p, output_days):
                output_to_delete.append(p)

    # Uploads
    uploads_dir = ROOT / "uploads"
    uploads_to_delete: list[Path] = []
    if uploads_dir.exists():
        for p in uploads_dir.iterdir():
            if _older_than(p, uploads_days):
                uploads_to_delete.append(p)

    return CleanupPlan(
        reports_to_delete=reports_to_delete,
        runs_to_delete=runs_to_delete,
        output_to_delete=output_to_delete,
        uploads_to_delete=uploads_to_delete,
    )


def apply(plan: CleanupPlan) -> int:
    """Delete every file in the plan. Returns the count actually removed."""
    removed = 0
    for group_name, group in (
        ("reports", plan.reports_to_delete),
        ("runs",    plan.runs_to_delete),
        ("output",  plan.output_to_delete),
        ("uploads", plan.uploads_to_delete),
    ):
        for p in group:
            try:
                p.unlink()
                removed += 1
                log.info("removed %s/%s", group_name, p.name)
            except OSError as exc:
                log.warning("could not delete %s (%s)", p, exc)
    return removed


def summarize(plan: CleanupPlan, applied: bool = False) -> None:
    verb = "removed" if applied else "would remove"
    print()
    print(f"{verb}:")
    print(f"  reports : {len(plan.reports_to_delete):4d} files")
    print(f"  runs    : {len(plan.runs_to_delete):4d} files")
    print(f"  output  : {len(plan.output_to_delete):4d} files")
    print(f"  uploads : {len(plan.uploads_to_delete):4d} files")
    print(f"  total   : {plan.total_count:4d} files")
    if not applied:
        try:
            mb = plan.total_bytes / (1024 * 1024)
            print(f"  freeable: {mb:.1f} MB")
        except OSError:
            pass
        print("\nDry-run only. Re-run with --apply to actually delete.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true",
                        help="Actually delete (default: dry run)")
    parser.add_argument("--reports", type=int, default=30,
                        help="Keep this many newest reports (default 30)")
    parser.add_argument("--runs-days", type=int, default=90,
                        help="Delete runs/* older than this (default 90)")
    parser.add_argument("--output-days", type=int, default=60,
                        help="Delete output/* older than this (default 60)")
    parser.add_argument("--uploads-days", type=int, default=30,
                        help="Delete uploads/* older than this (default 30)")
    parser.add_argument("--all-old", action="store_true",
                        help="Use a single --days threshold for everything")
    parser.add_argument("--days", type=int, default=30,
                        help="With --all-old: shared retention in days")
    args = parser.parse_args()

    if args.all_old:
        plan_obj = plan(
            keep_reports=0,
            runs_days=args.days,
            output_days=args.days,
            uploads_days=args.days,
        )
    else:
        plan_obj = plan(
            keep_reports=args.reports,
            runs_days=args.runs_days,
            output_days=args.output_days,
            uploads_days=args.uploads_days,
        )

    if args.apply:
        apply(plan_obj)
        summarize(plan_obj, applied=True)
    else:
        summarize(plan_obj, applied=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
