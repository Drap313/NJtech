"""Deterministic demo data: the spec's expected primary incident (section 14).

INCIDENT DG-I-0042 — HIGH
Load DG-204 no longer feasible after a 75-minute pickup delay.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from app.core.models import (
    ComplianceResult,
    CostComponent,
    Incident,
    IncidentSeverity,
    RecoveryPlan,
    RuleFailure,
    Verdict,
)


def demo_incident(now: datetime | None = None) -> Incident:
    now = now or datetime.now(timezone.utc)
    expires = now + timedelta(minutes=12)
    incident_id = "DG-I-0042"

    compliance = ComplianceResult(
        verdict=Verdict.FAIL,
        hard_failures=[
            RuleFailure(
                code="SHIFT_LIMIT",
                excess_minutes=41,
                detail="Projected 14-hour duty window exceeded by 41 minutes",
            )
        ],
        projected_clocks={"drive_left_minutes": 0, "shift_left_minutes": -41},
        rule_trace=[
            "us_federal_property_v1:duty_window_projection",
            "us_federal_property_v1:driving_limit_projection",
        ],
        input_freshness={"eld": "fresh", "gps": "fresh"},
    )

    relay = RecoveryPlan(
        plan_id="PLAN-RELAY-D03",
        incident_id=incident_id,
        title="Relay to D-03 at Exit 18",
        actions=["Relay load DG-204 to driver D-03 at Exit 18"],
        required_approvals=["dispatcher"],
        compliance_verdict=Verdict.PASS,
        delivery_eta=now + timedelta(hours=4, minutes=10),
        appointment_slack_minutes=22,
        hos_reserve_minutes=64,
        incremental_cost=286.0,
        cost_breakdown=[
            CostComponent(label="Reposition miles", amount_usd=118.0),
            CostComponent(label="Relay handling", amount_usd=120.0),
            CostComponent(label="Driver time", amount_usd=48.0),
        ],
        risk_score=24,
        assumptions=["D-03 confirms within 15 minutes", "Exit 18 lot has swap space"],
        expires_at=expires,
    )

    swap = RecoveryPlan(
        plan_id="PLAN-SWAP-D09",
        incident_id=incident_id,
        title="Swap to D-09",
        actions=["Reassign load DG-204 to driver D-09"],
        required_approvals=["dispatcher"],
        compliance_verdict=Verdict.PASS,
        delivery_eta=now + timedelta(hours=4, minutes=25),
        appointment_slack_minutes=7,
        hos_reserve_minutes=112,
        incremental_cost=411.0,
        cost_breakdown=[
            CostComponent(label="Reposition miles", amount_usd=236.0),
            CostComponent(label="Driver time", amount_usd=175.0),
        ],
        risk_score=31,
        assumptions=["D-09 accepts the assignment"],
        expires_at=expires,
    )

    hold = RecoveryPlan(
        plan_id="PLAN-HOLD-RESET",
        incident_id=incident_id,
        title="Hold for 10-hour reset",
        actions=["Hold load DG-204; driver takes qualifying reset"],
        required_approvals=["dispatcher"],
        compliance_verdict=Verdict.PASS,
        delivery_eta=now + timedelta(hours=13, minutes=45),
        appointment_slack_minutes=-575,
        hos_reserve_minutes=660,
        incremental_cost=1200.0,
        cost_breakdown=[
            CostComponent(label="Estimated service-failure cost", amount_usd=1200.0),
        ],
        risk_score=12,
        assumptions=["Customer accepts next-window delivery"],
        expires_at=expires,
    )

    return Incident(
        incident_id=incident_id,
        severity=IncidentSeverity.HIGH,
        load_id="DG-204",
        summary="Load DG-204 is no longer feasible after a 75-minute pickup delay.",
        cause="Projected duty-window overrun of 41 minutes.",
        customer_impact="Current plan misses delivery by 58 minutes.",
        compliance=compliance,
        recommended_plan_id=relay.plan_id,
        plans=[relay, swap, hold],
        approval_token=secrets.token_urlsafe(16),
        created_at=now,
        expires_at=expires,
    )


# Minimal synthetic driver snapshots for conversational queries.
DRIVERS: dict[str, dict] = {
    "D-01": {
        "name": "Marcus Webb",
        "duty_status": "DRIVING",
        "drive_left_minutes": 0,
        "shift_left_minutes": -41,
        "cycle_left_minutes": 1240,
        "endorsements": ["N"],
        "note": "Assigned to DG-204; duty-window overrun projected after pickup delay.",
    },
    "D-03": {
        "name": "Dana Ortiz",
        "duty_status": "ON_DUTY",
        "drive_left_minutes": 540,
        "shift_left_minutes": 620,
        "cycle_left_minutes": 2100,
        "endorsements": ["N", "H"],
        "note": "Available at Exit 18 staging lot; relay candidate for DG-204.",
    },
    "D-09": {
        "name": "Priya Nair",
        "duty_status": "OFF_DUTY",
        "drive_left_minutes": 660,
        "shift_left_minutes": 840,
        "cycle_left_minutes": 2800,
        "endorsements": ["N"],
        "note": "Finished reset 2 hours ago; 38 miles from DG-204 origin.",
    },
}
