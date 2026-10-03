"""Recovery solver: bounded candidate enumeration, hard-constraint filtering,
ranking, and independent re-check of every presented plan.

Rank order: hard compliance > service feasibility > incremental cost >
schedule risk > operational complexity.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..clock import minutes_between, utc
from ..config import get_rules
from ..forecasting.evaluator import evaluate_plan
from ..forecasting.planner import Planner, committed_actions
from ..models import Assignment, Driver
from ..state import Fleet
from .costs import incremental_cost

TIER_RANK = {"ON_TIME": 0, "AT_RISK": 1, "LATE": 2, "UNKNOWN": 3, "INFEASIBLE": 4}
KIND_LABEL = {"RELAY": "Relay", "SWAP_DRIVER": "Driver swap", "HOLD_FOR_RESET": "Hold for reset",
              "SWAP_TRACTOR": "Repower", "HOLD_AND_REAPPOINT": "Hold + new appointment", "CONTINUE": "Continue"}


def _equipment_free(fleet: Fleet, tractor_id: str | None, at: datetime) -> bool:
    if not tractor_id or tractor_id not in fleet.tractors:
        return False
    t = fleet.tractors[tractor_id]
    if t.status != "available":
        return False
    return not any(utc(b.start) <= at < utc(b.end) for b in t.maintenance)


def _mode_for(fleet: Fleet, d: Driver, dest_id: str, now: datetime) -> tuple[str, str | None] | None:
    """bobtail with the driver's own tractor if it is with them, else shuttle within range."""
    from ..config import get_cost_model
    t = fleet.tractors.get(d.tractor_id) if d.tractor_id else None
    if t and _equipment_free(fleet, t.id, now) and fleet.net.travel(d.position, t.position)[0] < 1:
        return ("bobtail", t.id)
    miles, _ = fleet.net.travel(d.position, dest_id)
    if miles <= get_cost_model().op("shuttle_max_miles"):
        return ("shuttle", None)
    return None


def generate_candidates(fleet: Fleet, asg: Assignment, now: datetime) -> tuple[list[list[dict]], list[dict]]:
    """Returns (action lists, pre-filtered rejections)."""
    load = fleet.loads[asg.load_id]
    route = fleet.net.routes[load.route_id]
    stage = asg.progress.stage
    m0 = asg.progress.route_minutes_done if stage == "IN_TRANSIT" else 0
    cands: list[list[dict]] = []
    rejected: list[dict] = []
    declined = asg.status == "DECLINED" or asg.driver_id is None

    if not declined:
        cands.append(committed_actions(asg))
    pool = [d for d in fleet.drivers.values()
            if d.id != asg.driver_id and d.availability == "available" and d.id not in asg.declined_by]
    for d in sorted(pool, key=lambda d: d.id):
        if stage in ("PLANNED", "AT_PICKUP"):
            m = _mode_for(fleet, d, load.origin_id, now)
            if m:
                cands.append([{"type": "SWAP_DRIVER", "driver_id": d.id, "mode": m[0], "tractor_id": m[1]}])
            else:
                rejected.append({"title": f"Swap to {d.id}", "codes": ["NO_EQUIPMENT_IN_RANGE"],
                                 "detail": f"{d.id} has no tractor with them and is beyond shuttle range of the shipper"})
        if declined:
            continue
        for w in route.waypoints[1:-1]:
            if not fleet.net.loc(w.loc).relay or w.minutes <= m0:
                continue
            m = _mode_for(fleet, d, w.loc, now)
            if m:
                cands.append([{"type": "RELAY", "point_id": w.loc, "driver_id": d.id, "mode": m[0], "tractor_id": m[1]}])
    if not declined and asg.relay is None:
        for w in reversed(route.waypoints):  # latest stop first: ties go to the stop nearest the customer
            if fleet.net.loc(w.loc).parking and w.minutes >= m0 and w.minutes < route.total_minutes:
                cands.append([{"type": "HOLD_FOR_RESET", "point_id": w.loc}])
        if stage == "AT_PICKUP":
            cands.append([{"type": "HOLD_FOR_RESET", "point_id": load.origin_id}])
    tractor = fleet.tractors.get(asg.tractor_id) if asg.tractor_id else None
    if not declined and tractor and tractor.status == "out_of_service":
        for t in sorted(fleet.tractors.values(), key=lambda t: t.id):
            if _equipment_free(fleet, t.id, now) and (not load.hazmat or t.hazmat_capable):
                cands.append([{"type": "SWAP_TRACTOR", "tractor_id": t.id}])
    return cands, rejected


def _expiry(pb, now: datetime) -> tuple[datetime, str]:
    R = get_rules()
    options = [(now + timedelta(minutes=R.recommendation_ttl), "policy TTL")]
    for leg in pb.legs:
        stale_at = utc(leg.driver.eld.observed_at) + timedelta(minutes=R.max_eld_age)
        if stale_at > now:
            options.append((stale_at, f"{leg.driver.id} ELD data goes stale"))
    if pb.decision_deadline and pb.decision_deadline > now:
        options.append((pb.decision_deadline, "latest departure for the replacement driver"))
    return min(options, key=lambda o: o[0])


def _plan_record(fleet: Fleet, asg: Assignment, pb, ev: dict, base_costs: dict, now: datetime) -> dict:
    inc, lines = incremental_cost(ev["cost_components"], base_costs)
    exp, why = _expiry(pb, now)
    approvals = ["dispatcher"]
    if any(a["type"] == "REQUEST_NEW_APPOINTMENT" for a in pb.actions):
        approvals.append("customer_appointment_change")
    comp = ev["compliance"]
    return {
        "kind": pb.kind,
        "kind_label": KIND_LABEL.get(pb.kind, pb.kind),
        "title": pb.title,
        "actions": pb.actions,
        "required_approvals": approvals,
        "compliance_verdict": comp["verdict"],
        "compliance": {k: comp[k] for k in ("verdict", "hard_failures", "warnings", "manual_review", "unknowns",
                                             "projected_clocks", "per_driver", "ruleset", "company_policy", "input_freshness")},
        "rule_trace": comp["rule_trace"],
        "service_tier": ev["feasibility"]["service_tier"],
        "pickup_eta": ev["pickup_eta"],
        "depart_eta": ev["depart_eta"],
        "delivery_eta": ev["delivery_eta"],
        "appointment_slack_minutes": ev["appointment_slack_minutes"],
        "hos_reserve_minutes": ev["hos_reserve_minutes"],
        "incremental_cost": inc,
        "cost_breakdown": lines,
        "risk_score": ev["risk"]["score"] if ev["risk"] else 0,
        "risk_band": ev["risk"]["band"] if ev["risk"] else "normal",
        "risk": ev["risk_by_driver"],
        "assumptions": ev["assumptions"],
        "required_stops": ev["required_stops"],
        "timeline": ev["timeline"],
        "drivers": ev["drivers"],
        "expires_at": exp.isoformat(),
        "expiry_reason": why,
        "complexity": len(ev["drivers"]) + len(pb.actions),
    }


def _rank_key(p: dict):
    return (TIER_RANK.get(p["service_tier"], 9), p["incremental_cost"], p["risk_score"], p["complexity"])


def _reject_reason(p: dict) -> dict:
    c = p["compliance"]
    codes = [h["code"] for h in c["hard_failures"]] + [u["code"] for u in c["unknowns"]] + [m["code"] for m in c["manual_review"]]
    detail = "; ".join(h.get("detail", "") for h in (c["hard_failures"] + c["unknowns"] + c["manual_review"])[:2])
    return {"title": p["title"], "kind": p["kind"], "verdict": p["compliance_verdict"], "codes": sorted(set(codes)), "detail": detail,
            "delivery_eta": p["delivery_eta"], "appointment_slack_minutes": p["appointment_slack_minutes"]}


def find_costed_alternatives(fleet: Fleet, assignment_id: str, now: datetime, max_options: int = 5) -> dict:
    now = utc(now)
    asg = fleet.assignments[assignment_id]
    load = fleet.loads[asg.load_id]
    planner = Planner(fleet)
    base_pb = planner.build(asg, now)
    base_ev = evaluate_plan(fleet, asg.id, base_pb, now)
    if base_pb.errors:
        base_costs = {k: (0 if not isinstance(v, list) else []) for k, v in base_ev["cost_components"].items()}
        base_costs["late_penalty"] = 0.0
    else:
        base_costs = base_ev["cost_components"]

    cand_actions, rejected = generate_candidates(fleet, asg, now)
    evaluated: list[dict] = []
    best_hold = None
    for actions in cand_actions:
        pb = planner.build(asg, now, actions)
        ev = evaluate_plan(fleet, asg.id, pb, now)
        rec = _plan_record(fleet, asg, pb, ev, base_costs, now)
        if pb.kind == "HOLD_FOR_RESET" and rec["compliance_verdict"] == "PASS":
            if best_hold is None or (rec["delivery_eta"] or "") < (best_hold[1]["delivery_eta"] or ""):
                best_hold = (actions, rec)
        evaluated.append(rec)

    # Reschedule variant of the best hold when the customer allows appointment changes.
    terms = fleet.customers[load.customer_id].terms
    if best_hold and terms.reschedule_allowed and (best_hold[1]["appointment_slack_minutes"] or 0) < 0:
        eta = datetime.fromisoformat(best_hold[1]["delivery_eta"])
        start = eta.replace(minute=0, second=0, microsecond=0)
        actions = best_hold[0] + [{"type": "REQUEST_NEW_APPOINTMENT", "start": start.isoformat(),
                                   "end": (start + timedelta(hours=3)).isoformat()}]
        pb = planner.build(asg, now, actions)
        evaluated.append(_plan_record(fleet, asg, pb, evaluate_plan(fleet, asg.id, pb, now), base_costs, now))

    committed = committed_actions(asg)
    current = next((p for p in evaluated if p["actions"] == committed), None)
    eligible = [p for p in evaluated if p["compliance_verdict"] == "PASS" and p["service_tier"] not in ("INFEASIBLE", "UNKNOWN")]
    if current in eligible:  # a legal current plan stays on the table (e.g. late but compliant: notify the customer)
        current["title"] = f"{current['title']} (notify customer)" if current["service_tier"] == "LATE" else current["title"]
        current["kind"], current["kind_label"] = "CONTINUE", "Continue"
    for p in evaluated:
        if p not in eligible and p is not current:
            rejected.append(_reject_reason(p))

    eligible.sort(key=_rank_key)
    presented, seen = [], set()
    for p in eligible:  # best plan of each strategy first, for a diverse set
        if p["kind"] not in seen:
            presented.append(p)
            seen.add(p["kind"])
    for p in eligible:  # top up to three options only when there are fewer strategies
        if len(presented) >= min(3, max_options):
            break
        if p not in presented:
            presented.append(p)
    presented = sorted(presented, key=_rank_key)[:max_options]

    # Independent re-check: rebuild every presented plan from its actions and re-run compliance.
    for p in presented:
        pb = Planner(fleet).build(fleet.assignments[assignment_id], now, p["actions"])
        ev = evaluate_plan(fleet, assignment_id, pb, now)
        p["recheck"] = {"verdict": ev["compliance"]["verdict"], "at": now.isoformat(),
                        "match": ev["compliance"]["verdict"] == p["compliance_verdict"]}
    presented = [p for p in presented if p["recheck"]["verdict"] == "PASS"]
    also = [{"title": p["title"], "service_tier": p["service_tier"], "incremental_cost": p["incremental_cost"],
             "appointment_slack_minutes": p["appointment_slack_minutes"]} for p in eligible if p not in presented][:8]
    return {
        "assignment_id": assignment_id,
        "evaluated_at": now.isoformat(),
        "current_plan": current,
        "plans": presented,
        "also_considered": also,
        "rejected": rejected[:12],
        "candidates_evaluated": len(evaluated),
    }
