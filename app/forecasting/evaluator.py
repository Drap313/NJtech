"""Assignment feasibility: legal, feasible, economical and low-risk are reported
as separate outputs from the same simulated timeline."""
from __future__ import annotations

from datetime import datetime

from ..clock import minutes_between, utc
from ..compliance.engine import check_plan
from ..compliance.hos import DRIVING, SimResult
from ..config import Rules, get_rules
from ..optimization.costs import plan_cost_components
from ..state import Fleet
from .planner import PlanBuild, Planner
from .risk import score_schedule_risk


def _iso(dt):
    return dt.isoformat() if dt else None


def serialize_timeline(sim: SimResult, driver_id: str, role: str) -> list[dict]:
    return [{
        "driver_id": driver_id, "role": role, "start": e.start.isoformat(), "end": e.end.isoformat(), "status": e.status,
        "label": e.label, "stop": e.stop, "loc": e.loc, "minutes": e.minutes, "miles": round(e.miles, 1),
        "a": e.a, "b": e.b, "route_from": e.route_from, "route_to": e.route_to,
    } for e in sim.entries]


def evaluate_plan(fleet: Fleet, asg_id: str, pb: PlanBuild, now: datetime, rules: Rules | None = None) -> dict:
    R = rules or get_rules()
    now = utc(now)
    asg = fleet.assignments[asg_id]
    load = fleet.loads[asg.load_id]
    terms = fleet.customers[load.customer_id].terms
    compliance = check_plan(fleet, pb, load, now, R)

    window = pb.delivery_window or load.delivery_window
    m = pb.marks
    delivery_eta = m.get("delivery_arrival")
    slack = round(minutes_between(delivery_eta, utc(window.end))) if delivery_eta else None
    pickup_eta = m.get("pickup_arrival")
    pickup_slack = None
    feas_fail, feas_warn = [], []
    if asg.progress.stage == "PLANNED" and pickup_eta:
        pickup_slack = round(minutes_between(pickup_eta, utc(load.pickup_window.end)))
        if pickup_slack < 0:
            feas_fail.append({"code": "PICKUP_WINDOW_MISSED", "late_minutes": -pickup_slack, "at": load.pickup_window.end.isoformat(),
                              "detail": f"pickup arrival {-pickup_slack} min after the window closes"})
    if slack is not None:
        if slack < 0:
            feas_fail.append({"code": "DELIVERY_LATE", "late_minutes": -slack, "at": window.end.isoformat(),
                              "detail": f"delivery arrival {-slack} min after the appointment window closes"})
        elif slack < R.low_slack_warn:
            feas_warn.append({"code": "LOW_SLACK", "remaining_minutes": slack, "detail": f"only {slack} min of appointment slack"})
    if pb.errors:
        tier = "INFEASIBLE"
    elif slack is None:
        tier = "UNKNOWN"
    elif slack < 0:
        tier = "LATE"
    elif slack < R.min_service_slack:
        tier = "AT_RISK"
    else:
        tier = "ON_TIME"

    # Legal reserve across every driver who still drives in this plan.
    reserves = [l.sim.reserve["reserve"] for l in pb.legs if l.sim and l.sim.reserve and l.sim.last_drive_end and l.sim.last_drive_end > now]
    hos_reserve = min(reserves) if reserves else None

    risk_by_driver = {}
    changed = pb.kind not in ("CONTINUE",)
    for l in pb.legs:
        if l.carries_cargo and l.sim:
            risk_by_driver[l.driver.id] = score_schedule_risk(l.driver, l.sim, now, fleet.net, changed=changed or l.role != "primary")
    risk = max(risk_by_driver.values(), key=lambda r: r["score"]) if risk_by_driver else None

    # First point of failure, across compliance and service.
    candidates = [(h["first_at"], h["code"], h["detail"]) for h in compliance["hard_failures"] if h.get("first_at")]
    candidates += [(f["at"], f["code"], f["detail"]) for f in feas_fail]
    first = min(candidates, key=lambda c: utc(datetime.fromisoformat(c[0]))) if candidates else None

    stops = []
    for l in pb.legs:
        if not l.sim:
            continue
        for e in l.sim.entries:
            if e.stop in ("break", "reset", "handoff") or (e.stop == "wait" and e.minutes >= 15 and e.end > now):
                stops.append({"driver_id": l.driver.id, "type": e.stop, "label": e.label, "start": e.start.isoformat(),
                              "end": e.end.isoformat(), "minutes": e.minutes, "loc": e.loc, "at": e.a})
    stops.sort(key=lambda s: s["start"])

    costs = plan_cost_components(pb, terms, slack, now, risk["score"] if risk else 0)
    timeline = []
    for l in pb.legs:
        if l.sim:
            timeline += serialize_timeline(l.sim, l.driver.id, l.role)
    return {
        "assignment_id": asg.id,
        "load_id": load.id,
        "plan_kind": pb.kind,
        "plan_title": pb.title,
        "actions": pb.actions,
        "at": now.isoformat(),
        "drivers": [{"driver_id": l.driver.id, "role": l.role, "tractor_id": l.tractor_id} for l in pb.legs],
        "compliance": compliance,
        "verdict": compliance["verdict"],
        "feasibility": {"service_tier": tier, "hard_failures": feas_fail, "warnings": feas_warn, "first_failure":
                        {"at": first[0], "code": first[1], "detail": first[2]} if first else None},
        "pickup_eta": _iso(pickup_eta),
        "pickup_slack_minutes": pickup_slack,
        "depart_eta": _iso(m.get("depart_pickup")),
        "delivery_eta": _iso(delivery_eta),
        "delivered_eta": _iso(m.get("delivered")),
        "delivery_window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "appointment_slack_minutes": slack,
        "hos_reserve_minutes": hos_reserve,
        "required_stops": stops,
        "risk": risk,
        "risk_by_driver": risk_by_driver,
        "cost_components": costs,
        "assumptions": pb.assumptions,
        "errors": pb.errors,
        "timeline": timeline,
    }


def evaluate_assignment(fleet: Fleet, assignment_id: str, at_time: datetime, rules: Rules | None = None) -> dict:
    """Stable contract: everything downstream consumes this output."""
    asg = fleet.assignments[assignment_id]
    pb = Planner(fleet, rules).build(asg, at_time)
    return evaluate_plan(fleet, assignment_id, pb, at_time, rules)


def plan_sims(fleet: Fleet, assignment_id: str, at_time: datetime) -> PlanBuild:
    return Planner(fleet).build(fleet.assignments[assignment_id], at_time)


def drives_after(sim: SimResult, t: datetime) -> bool:
    return any(e.status == DRIVING and e.end > t for e in sim.entries)
