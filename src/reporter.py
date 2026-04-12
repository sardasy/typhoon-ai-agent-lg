"""
Reporter — generates test reports from state data.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


class Reporter:
    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )

    def _generate_html(self, context: dict, timestamp: str) -> str:
        template = self.env.get_template("report.html")
        html = template.render(**context)
        path = self.output_dir / f"report_{timestamp}.html"
        path.write_text(html, encoding="utf-8")
        logger.info(f"HTML report: {path}")
        return str(path)

    def _generate_xray(self, context: dict, timestamp: str) -> str:
        xray_results = []
        for r in context.get("results", []):
            status_map = {"pass": "PASSED", "fail": "FAILED", "skipped": "SKIPPED", "error": "FAILED"}
            xray_results.append({
                "testKey": r.get("scenario_id", ""),
                "status": status_map.get(r.get("status", ""), "TODO"),
                "comment": r.get("fail_reason", ""),
            })
        payload = {
            "testExecutionKey": f"THAA-{timestamp}",
            "info": {"summary": context.get("plan_goal", ""), "startDate": context.get("start_time", "")},
            "tests": xray_results,
        }
        path = self.output_dir / f"xray_{timestamp}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        return str(path)
