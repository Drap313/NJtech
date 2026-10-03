"""RealCore: implements the same CoreClient interface FakeCore does, but
sourced from the real engine (app.feasibility / app.optimization /
app.fleet_state) instead of fixtures. Subclasses FakeCore to reuse its
execute_approved_plan / record_override / get_incident logic verbatim --
that logic is generic and never touches fixtures directly."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from app.core.client import FakeCore
from app.core.models import (
    ComplianceResult, CostComponent, Incident, IncidentSeverity,
    RecoveryPlan, RuleFailure, Verdict,
)
from app.fleet_state import FleetState, load_fleet_state
from app.feasibility.assignment import evaluate_assignment
from app.optimization.alternatives import find_costed_alternatives

_VERDICT_MAP = {"legal": Verdict.PASS, "illegal": Verdict.FAIL, "manual_review": Verdict.MANUAL_REVIEW}
INCIDENT_TTL_MINUTES = 12

class RealCore(FakeCore):
    def __init__(self, fleet_state: FleetState | None = None) -> None:
        super().__init__()
        self.fleet_state = fleet_state or load_fleet_state()
        self._traces: dict[str, list[str]] = {}

    def _compliance_to_model(self, compliance: dict) -> ComplianceResult:
        hard = [RuleFailure(code=v["rule"], excess_minutes=v.get("deficit_minutes"), detail=v.get("detail"))
                for v in compliance.get("violations", [])]
        warn = [RuleFailure(code=w["rule"], detail=w.get("detail"))
                for w in compliance.get("warnings", [])]
        fresh = compliance.get("data_freshness", {})
        input_freshness = {}
        if "eld_age_minutes" in fresh:
            input_freshness["eld"] = "fresh" if fresh["eld_age_minutes"] < 15 else "stale"
        if "location_age_minutes" in fresh:
            input_freshness["location"] = "fresh" if fresh["location_age_minutes"] < 20 else "stale"
        return ComplianceResult(
            verdict=_VERDICT_MAP.get(compliance.get("verdict"), Verdict.UNKNOWN),
            hard_failures=hard, warnings=warn,
            rule_trace=[compliance.get("verdict", "")],
            input_freshness=input_freshness,
        )

    def _plan_to_model(self, plan: dict, incident_id: str, load, now: datetime) -> RecoveryPlan:
        delay = plan.get("delivery_delay_minutes") or 0
        delivery_eta = load.delivery_window_end + timedelta(minutes=delay) if delay else load.delivery_window_end
        title_map = {
            "driver_swap": f"Swap to {plan['steps'][0].get('driver', '?')}",
            "driver_relay": f"Relay via {plan['steps'][0].get('to', '?')}",
            "hold_for_reset": "Hold for 10-hour reset",
            "delivery_window_adjustment": "Ask customer to move delivery window",
            "leave_unassigned": "Leave load unassigned",
            "confirm": "Confirm current plan",
        }
        return RecoveryPlan(
            plan_id=plan["plan_id"], incident_id=incident_id,
            title=title_map.get(plan["type"], plan["type"]),
            actions=[str(s) for s in plan.get("steps", [])],
            required_approvals=["dispatcher"],
            compliance_verdict=Verdict.PASS,
            delivery_eta=delivery_eta,
            appointment_slack_minutes=-delay if delay else None,
            hos_reserve_minutes=plan.get("minimum_hos_reserve_minutes"),
            incremental_cost=plan.get("added_cost", 0.0),
            cost_breakdown=[CostComponent(label="Incremental cost (not yet itemized)",
                                           amount_usd=plan.get("added_cost", 0.0))],
            risk_score=plan.get("schedule_risk_score", 0),
            assumptions=[],
            expires_at=now + timedelta(minutes=INCIDENT_TTL_MINUTES),
        )

    def open_incident_for_assignment(self, driver_id: str, load_id: str) -> Incident:
        fs = self.fleet_state
        driver, load = fs.driver(driver_id), fs.load(load_id)
        assignment_id = next((aid for aid, a in fs.assignments.items() if a.load_id == load_id), None)
        vehicle = fs.vehicle(fs.assignments[assignment_id].vehicle_id) if assignment_id else None
        trailer = fs.trailer(fs.assignments[assignment_id].trailer_id) if assignment_id else None

        ev = evaluate_assignment(driver, load, vehicle, trailer, proposed_start=fs.now, now=fs.now)
        compliance_model = self._compliance_to_model(ev["compliance"])
        now = fs.now
        incident_id = f"DG-I-{load_id}-{secrets.token_hex(3)}"

        if ev["legally_compliant"] and ev["operationally_feasible"]:
            plans_models, recommended_id = [], None
            severity = IncidentSeverity.LOW
            summary, cause = f"Load {load_id} is clear on hours and fatigue flags.", "No issue detected."
        else:
            raw_plans = find_costed_alternatives(assignment_id, fs) if assignment_id else []
            plans_models = [self._plan_to_model(p, incident_id, load, now) for p in raw_plans]
            recommended_id = next((p["plan_id"] for p in raw_plans if p.get("recommended")),
                                   raw_plans[0]["plan_id"] if raw_plans else None)
            severity = IncidentSeverity.HIGH if not ev["legally_compliant"] else IncidentSeverity.MEDIUM
            summary = f"Load {load_id} is no longer feasible: {ev['reason']}"
            cause = ev["reason"] or "Unknown"

        incident = Incident(
            incident_id=incident_id, severity=severity, load_id=load_id,
            summary=summary, cause=cause,
            customer_impact="See recommended plan for projected delay.",
            compliance=compliance_model, recommended_plan_id=recommended_id or "",
            plans=plans_models, approval_token=secrets.token_urlsafe(16),
            created_at=now, expires_at=now + timedelta(minutes=INCIDENT_TTL_MINUTES),
        )
        self.incidents[incident.incident_id] = incident
        self._traces[incident.incident_id] = [
            f"{now.isoformat()} evaluate_assignment({driver_id}, {load_id}) -> "
            f"legal={ev['legally_compliant']} feasible={ev['operationally_feasible']}",
            f"find_costed_alternatives -> {len(plans_models)} ranked option(s)",
            f"recommend {recommended_id}" if recommended_id else "no recommendation (clear)",
        ]
        return incident

    def get_driver_status(self, driver_id: str) -> dict | None:
        try:
            d = self.fleet_state.driver(driver_id.lower())
        except KeyError:
            return None
        return {
            "name": d.name, "duty_status": d.duty_status.upper(),
            "drive_left_minutes": d.drive_minutes_remaining,
            "shift_left_minutes": d.shift_minutes_remaining,
            "cycle_left_minutes": d.cycle_minutes_remaining,
            "endorsements": d.endorsements,
            "note": f"At {d.current_location}; reported status: {d.reported_status}.",
        }

    def get_decision_trace(self, incident_id: str) -> list[str]:
        return self._traces.get(incident_id, [])
