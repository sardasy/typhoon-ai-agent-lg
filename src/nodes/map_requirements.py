"""
Node: map_requirements

Maps a parsed TSE topology to concrete test requirements.
Each topology has common tests plus topology-specific tests.
"""

from __future__ import annotations

import logging
from typing import Any

from ..state import AgentState, VerificationRequirement, make_event

logger = logging.getLogger(__name__)


def _common_requirements(parsed: dict) -> list[VerificationRequirement]:
    """Requirements common to all topologies."""
    reqs = []
    # Output voltage regulation
    sources = parsed.get("sources", {})
    first_source_val = next(iter(sources.values()), 48.0)
    reqs.append(VerificationRequirement(
        req_id="REQ_COMMON_001",
        name="output_voltage_regulation",
        metric="output_voltage",
        target_value=first_source_val,
        tolerance_fraction=0.05,
        duration_s=0.5,
        sampling_rate_hz=50000,
        topology_specific=False,
    ))
    return reqs


def _boost_buck_requirements(parsed: dict) -> list[VerificationRequirement]:
    """Additional requirements for boost/buck converters."""
    return [
        VerificationRequirement(
            req_id="REQ_DCDC_001",
            name="output_ripple",
            metric="ripple",
            target_value=0.02,
            tolerance_fraction=0.0,
            duration_s=0.1,
            sampling_rate_hz=100000,
            topology_specific=True,
        ),
        VerificationRequirement(
            req_id="REQ_DCDC_002",
            name="settling_time",
            metric="settling_time",
            target_value=0.050,
            tolerance_fraction=0.0,
            duration_s=0.5,
            sampling_rate_hz=50000,
            topology_specific=True,
        ),
    ]


def _inverter_requirements(parsed: dict) -> list[VerificationRequirement]:
    """Additional requirements for inverter topologies."""
    return [
        VerificationRequirement(
            req_id="REQ_INV_001",
            name="rms_voltage_verification",
            metric="rms_voltage",
            target_value=230.0,
            tolerance_fraction=0.05,
            duration_s=0.2,
            sampling_rate_hz=50000,
            topology_specific=True,
        ),
    ]


def _dab_requirements(parsed: dict) -> list[VerificationRequirement]:
    """Additional requirements for DAB topology."""
    return [
        VerificationRequirement(
            req_id="REQ_DAB_001",
            name="phase_shift_control",
            metric="phase_shift",
            target_value=0.0,
            tolerance_fraction=0.1,
            duration_s=0.5,
            sampling_rate_hz=100000,
            topology_specific=True,
        ),
        VerificationRequirement(
            req_id="REQ_DAB_002",
            name="soft_switching_verification",
            metric="soft_switching",
            target_value=1.0,
            tolerance_fraction=0.0,
            duration_s=0.2,
            sampling_rate_hz=200000,
            topology_specific=True,
        ),
    ]


TOPOLOGY_MAP = {
    "boost": _boost_buck_requirements,
    "buck": _boost_buck_requirements,
    "inverter": _inverter_requirements,
    "dab": _dab_requirements,
}


async def map_requirements(state: AgentState) -> dict[str, Any]:
    """Map parsed TSE to test requirements based on topology."""
    parsed = state.get("parsed_tse")
    if not parsed:
        return {
            "error": "No parsed TSE data available",
            "events": [make_event("map_requirements", "error", "No parsed TSE data")],
        }

    topology = parsed.get("topology", "unknown")
    reqs = _common_requirements(parsed)

    topo_fn = TOPOLOGY_MAP.get(topology)
    if topo_fn:
        reqs.extend(topo_fn(parsed))

    req_dicts = [r.model_dump() for r in reqs]
    msg = f"Mapped {len(reqs)} test requirements for topology={topology}"
    logger.info(msg)

    return {
        "test_requirements": req_dicts,
        "events": [make_event("map_requirements", "plan", msg, {
            "requirement_count": len(reqs),
            "topology": topology,
            "req_names": [r.name for r in reqs],
        })],
    }
