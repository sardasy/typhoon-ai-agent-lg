"""
Node: generate_report

Final node — stops simulation, generates HTML report from all results.
"""

from __future__ import annotations

from typing import Any

from ..state import AgentState, make_event
from ..reporter import Reporter
from .load_model import get_hil


async def generate_report(state: AgentState) -> dict[str, Any]:
    """Stop simulation and generate report."""

    # Stop HIL
    hil = get_hil()
    await hil.execute("hil_control", {"action": "stop"})

    # Build minimal plan-like dict for reporter
    results = state.get("results", [])
    total = len(results)
    passed = sum(1 for r in results if r.get("status") == "pass")
    failed = sum(1 for r in results if r.get("status") == "fail")

    r = Reporter(output_dir="reports")

    # Build context for template
    import time
    context = {
        "plan_id": f"plan_{int(time.time())}",
        "plan_goal": state.get("goal", ""),
        "plan_strategy": state.get("plan_strategy", ""),
        "total": total,
        "passed": passed,
        "failed": failed,
        "conditional_pass": 0,
        "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "errors": sum(1 for r in results if r.get("status") == "error"),
        "pass_rate": f"{passed / total * 100:.1f}" if total else "0",
        "results": _format_results(state),
        "standard_coverage": state.get("standard_coverage", {}),
        "start_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "end_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "device_mode": state.get("device_mode", ""),
        "active_preset": state.get("active_preset", ""),
    }

    report_path = r._generate_html(context, time.strftime("%Y%m%d_%H%M%S"))

    summary = f"Total: {total}, Passed: {passed}, Failed: {failed}, Rate: {context['pass_rate']}%"

    return {
        "report_path": report_path,
        "events": [make_event("report", "report", f"Report: {report_path} — {summary}")],
    }


def _format_results(state: AgentState) -> list[dict]:
    """Format results for Jinja2 template."""
    results = state.get("results", [])
    scenarios = state.get("scenarios", [])
    sc_map = {s.get("scenario_id", ""): s for s in scenarios}

    rows = []
    for r in results:
        sid = r.get("scenario_id", "")
        sc = sc_map.get(sid, {})
        rows.append({
            "scenario_id": sid,
            "name": sc.get("name", sid),
            "category": sc.get("category", ""),
            "status": r.get("status", ""),
            "duration_s": round(r.get("duration_s", 0), 2),
            "fail_reason": r.get("fail_reason", ""),
            "retry_count": r.get("retry_count", 0),
            "root_cause": r.get("root_cause", ""),
            "waveform_stats": r.get("waveform_stats", []),
        })
    return rows
