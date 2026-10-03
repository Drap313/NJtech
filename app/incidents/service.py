"""Incident lifecycle, approval gate and approved-plan execution.

Automatic: open/update/merge/close incidents, request driver status, draft
messages. Human approval required: any assignment change, relay, equipment
swap, appointment change, external commitment, override.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..clock import SimClock, minutes_between, utc
from ..config import policy_versions
from ..db import Store
from ..forecasting.evaluator import evaluate_assignment, evaluate_plan
from ..forecasting.planner import Planner
from ..models import Hold, Relay
from ..optimization.solver import find_costed_alternatives
from ..state import Fleet

SEV = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ET = ZoneInfo("America/New_York")


class ApprovalError(Exception):
    pass


def _hm(minutes: float) -> str:
    m = int(round(abs(minutes)))
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m} min"


def _clock(dt_iso: str | None) -> str:
    return datetime.fromisoformat(dt_iso).astimezone(ET).strftime("%a %H:%M %Z") if dt_iso else "n/a"


def assess(ev: dict, asg, now: datetime) -> tuple[str | None, list[str]]:
    sev, reasons = None, []

    def bump(s, why):
        nonlocal sev
        reasons.append(why)
        if sev is None or SEV.index(s) > SEV.index(sev):
            sev = s
    if asg.status == "DECLINED":
        bump("MEDIUM", "driver declined the assignment")
    verdict, tier = ev["verdict"], ev["feasibility"]["service_tier"]
    if verdict == "FAIL":
        first = ev["feasibility"]["first_failure"]
        soon = first and minutes_between(now, datetime.fromisoformat(first["at"])) <= 60
        bump("CRITICAL" if soon else "HIGH", "hard compliance failure")
    if tier == "LATE":
        bump("HIGH", "projected late delivery")
    if verdict == "UNKNOWN":
        bump("MEDIUM", "stale or missing input data")
    if verdict == "MANUAL_REVIEW":
        bump("MEDIUM", "manual review required")
    if tier == "AT_RISK" and verdict == "PASS":
        bump("MEDIUM", "appointment slack below policy minimum")
    if ev.get("risk") and ev["risk"]["band"] == "critical" and verdict == "PASS":
        bump("MEDIUM", "critical schedule risk")
    return sev, reasons


def _cause(ev: dict) -> str:
    c = ev["compliance"]
    if c["hard_failures"]:
        h = c["hard_failures"][0]
        if h["code"] == "SHIFT_LIMIT":
            return f"projected duty-window overrun of {h['excess_minutes']} minutes ({h['citation']})"
        if h["code"] == "DRIVE_LIMIT":
            return f"projected 11-hour driving overrun of {h['excess_minutes']} minutes ({h['citation']})"
        if h["code"] == "CYCLE_LIMIT":
            return f"projected cycle overrun of {h['excess_minutes']} minutes ({h['citation']})"
        return f"{h['code']}: {h.get('detail', '')}"
    if c["unknowns"]:
        return "; ".join(u["detail"] for u in c["unknowns"])
    if c["manual_review"]:
        return "; ".join(m["detail"] for m in c["manual_review"])
    ff = ev["feasibility"]
    if ff["hard_failures"]:
        return ff["hard_failures"][0]["detail"]
    if ff["warnings"]:
        return ff["warnings"][0]["detail"]
    return "plan changed"


def _title(ev: dict, asg, trigger: dict | None) -> str:
    load = ev["load_id"]
    if asg.status == "DECLINED":
        return f"Load {load} needs a driver: assignment declined."
    if trigger and trigger["type"] == "PICKUP_DELAYED":
        return f"Load {load} is no longer feasible after a {trigger['payload'].get('delay_minutes')}-minute pickup delay."
    if ev["verdict"] == "UNKNOWN":
        return f"Load {load} cannot be verified: input data is stale."
    if ev["verdict"] == "FAIL":
        return f"Load {load} current plan fails compliance."
    if ev["feasibility"]["service_tier"] == "LATE":
        return f"Load {load} is projected to miss delivery."
    return f"Load {load} plan is fragile."


def _impact(ev: dict, alts: dict | None) -> str:
    slack = ev["appointment_slack_minutes"]
    parts = []
    if ev["verdict"] == "UNKNOWN":
        last = f"; last projection from stale data showed {slack} min of slack" if slack is not None else ""
        return "cannot be confirmed until fresh data arrives" + last
    hos = {"SHIFT_LIMIT", "DRIVE_LIMIT", "CYCLE_LIMIT", "BREAK_REQUIRED", "COMPANY_DRIVE_LIMIT", "COMPANY_WINDOW_LIMIT", "COMPANY_MIN_RESERVE"}
    codes = {h["code"] for h in ev["compliance"]["hard_failures"]}
    if ev["verdict"] == "FAIL" and not codes & hos:
        parts.append(f"current plan cannot run as planned ({', '.join(sorted(codes))})")
    elif ev["verdict"] == "FAIL":
        if slack is not None and slack >= 0:
            parts.append(f"current plan reaches the customer only by breaking HOS ({slack} min slack as planned)")
        elif slack is not None:
            parts.append(f"current plan misses delivery by {_hm(slack)} even while breaking HOS")
        hold = next((p for p in (alts or {}).get("plans", []) if p["kind"] == "HOLD_FOR_RESET"), None)
        if hold and hold["appointment_slack_minutes"] is not None and hold["appointment_slack_minutes"] < 0:
            parts.append(f"a compliant hold misses delivery by {_hm(hold['appointment_slack_minutes'])}")
    elif slack is not None and slack < 0:
        parts.append(f"current plan misses delivery by {_hm(slack)}")
    elif slack is not None:
        parts.append(f"current plan arrives with {slack} min of slack")
    return "; ".join(parts) or "none projected yet"


def _option_line(p: dict) -> str:
    s = p["appointment_slack_minutes"]
    if s is None:
        when = "ETA unknown"
    elif s >= 0:
        when = f"on time ({s} min slack)"
    else:
        when = f"delivery +{_hm(s)}"
    return f"{p['title']}: {p['compliance_verdict'].lower()}, {when}, reserve {p['hos_reserve_minutes']} min, +${p['incremental_cost']:,.0f}"


def summary_text(inc: dict, now: datetime) -> str:
    lines = [f"INCIDENT {inc['id']} - {inc['severity']}", "", inc["title"], f"Cause: {inc['cause']}.",
             f"Customer impact: {inc['customer_impact']}."]
    plans = inc.get("plans") or []
    if plans:
        r = plans[0]
        s = r["appointment_slack_minutes"]
        lines += ["", f"Recommended: {r['title']}",
                  f"  Delivery: {'on time, with ' + str(s) + ' minutes of slack' if s is not None and s >= 0 else 'late by ' + _hm(s or 0)} ({_clock(r['delivery_eta'])})",
                  f"  Legal reserve: {r['hos_reserve_minutes']} minutes",
                  f"  Incremental cost: ${r['incremental_cost']:,.0f}",
                  f"  Schedule risk: {r['risk_score']} ({r['risk_band']})"]
        if len(plans) > 1:
            lines += ["", "Alternatives:"] + [f"  {_option_line(p)}" for p in plans[1:]]
        exp = minutes_between(now, datetime.fromisoformat(r["expires_at"]))
        lines += ["", "Required action: dispatcher approval.", f"Recommendation expires in {max(0, int(exp))} minutes ({r['expiry_reason']})."]
    elif inc.get("auto_actions"):
        lines += ["", "Automatic actions: " + "; ".join(a["what"] for a in inc["auto_actions"])]
    return "\n".join(lines)


def _signature(sev, ev, alts) -> list:
    eta = ev["delivery_eta"]
    eta_bucket = int(utc(datetime.fromisoformat(eta)).timestamp() // 900) if eta else None
    rec = (alts or {}).get("plans", [None])
    rec = rec[0] if rec else None
    return [sev, ev["verdict"], ev["feasibility"]["service_tier"], eta_bucket,
            rec["title"] if rec else None, int(rec["incremental_cost"] // 50) if rec else None]


def _plans_expired(inc: dict, now: datetime) -> bool:
    return any(datetime.fromisoformat(p["expires_at"]) <= now for p in inc.get("plans", []))


def reconcile(store: Store, fleet: Fleet, asg, ev: dict, now: datetime, trigger: dict | None = None) -> dict:
    now = utc(now)
    sev, reasons = assess(ev, asg, now)
    existing = store.open_incident_for(asg.id)
    if sev is None:
        if existing:
            existing.update(status="RESOLVED", updated_at=now.isoformat())
            existing["history"].append({"at": now.isoformat(), "what": "Condition cleared on re-evaluation; auto-resolved."})
            store.put_incident(existing)
            store.audit("incident_resolved", f"incident:{existing['id']}", {"reason": "condition cleared"}, now.isoformat())
            return {"action": "resolved", "incident_id": existing["id"]}
        return {"action": "none"}

    needs_options = ev["verdict"] == "FAIL" or ev["feasibility"]["service_tier"] in ("LATE", "AT_RISK") or asg.status == "DECLINED"
    alts = find_costed_alternatives(fleet, asg.id, now) if needs_options else None
    sig = _signature(sev, ev, alts)
    trig_id = trigger["event_id"] if trigger else None
    if existing and existing["signature"] == sig and not _plans_expired(existing, now):
        existing["suppressed_count"] = existing.get("suppressed_count", 0) + 1
        if trig_id and trig_id not in existing["trigger_events"] and trigger["type"] not in ("ELD_UPDATED", "GPS_UPDATED", "REFORECAST"):
            existing["trigger_events"].append(trig_id)
        store.put_incident(existing)
        return {"action": "suppressed", "incident_id": existing["id"]}

    material = not existing or existing["signature"] != sig
    if existing:
        inc = existing
        inc["revision"] += 1
        for p in inc.get("plans", []):
            store.set_plan_status(p["plan_id"], "SUPERSEDED")
    else:
        n = store.next_seq("incident_seq")
        inc = {"id": f"DG-I-{n:04d}", "assignment_id": asg.id, "load_id": asg.load_id, "status": "OPEN", "revision": 1,
               "created_at": now.isoformat(), "trigger_events": [], "history": [], "auto_actions": [], "suppressed_count": 0,
               "notifications": 0, "explanation": None}
        if trigger:
            inc["title_trigger"] = {"type": trigger["type"], "payload": trigger["payload"]}
    if trig_id and trig_id not in inc["trigger_events"]:
        inc["trigger_events"].append(trig_id)
    title_trigger = inc.get("title_trigger") and {"type": inc["title_trigger"]["type"], "payload": inc["title_trigger"]["payload"]}
    inc.update(
        severity=sev, reasons=reasons, updated_at=now.isoformat(), signature=sig,
        title=_title(ev, asg, title_trigger), cause=_cause(ev),
        customer_impact=_impact(ev, alts) if asg.status != "DECLINED" else
        f"no driver for the {_clock(fleet.loads[asg.load_id].pickup_window.start.isoformat())} pickup until a replacement is approved",
        policy_versions=policy_versions(),
        evaluation={k: ev[k] for k in ("verdict", "plan_title", "pickup_eta", "depart_eta", "delivery_eta", "appointment_slack_minutes",
                                       "hos_reserve_minutes", "delivery_window", "risk", "feasibility", "at")}
        | {"hard_failures": ev["compliance"]["hard_failures"], "unknowns": ev["compliance"]["unknowns"],
           "warnings": ev["compliance"]["warnings"], "rule_trace": ev["compliance"]["rule_trace"], "timeline": ev["timeline"]},
        inputs_snapshot=_inputs_snapshot(fleet, asg),
        current_plan=(alts or {}).get("current_plan") and {k: alts["current_plan"][k] for k in ("title", "compliance_verdict", "compliance", "delivery_eta", "appointment_slack_minutes", "hos_reserve_minutes")},
        plans=[], rejected=(alts or {}).get("rejected", []), also_considered=(alts or {}).get("also_considered", []),
    )
    for i, p in enumerate((alts or {}).get("plans", []), 1):
        p = dict(p, plan_id=f"{inc['id']}-R{inc['revision']}-P{i}", incident_id=inc["id"])
        inc["plans"].append(p)
        store.put_plan(p, "PROPOSED")
    inc["recommended_plan_id"] = inc["plans"][0]["plan_id"] if inc["plans"] else None

    # Automatic, non-committal actions.
    if ev["verdict"] == "UNKNOWN":
        for u in ev["compliance"]["unknowns"]:
            if u["code"] == "STALE_ELD":
                body = f"Dispatch: we lost your ELD feed. Please confirm location, duty status and hours used for {asg.load_id}."
                if request_driver_confirmation(store, u["driver_id"], body, f"status-req:{u['driver_id']}:{inc['id']}", now, inc["id"]):
                    inc["auto_actions"].append({"at": now.isoformat(), "what": f"Requested status confirmation from {u['driver_id']}"})
    inc["history"].append({"at": now.isoformat(), "what": f"{'Opened' if inc['revision'] == 1 else 'Updated'} (rev {inc['revision']}): {inc['cause']}"})
    inc["summary"] = summary_text(inc, now)
    if material:
        inc["notifications"] += 1
        store.outbox_add("dispatcher", "dispatch-desk", inc["summary"], "queued", inc["id"], False, f"notify:{inc['id']}:{inc['revision']}", now.isoformat())
    store.put_incident(inc)
    store.audit("incident_opened" if inc["revision"] == 1 else "incident_updated", f"incident:{inc['id']}",
                {"severity": sev, "reasons": reasons, "verdict": ev["verdict"], "signature": sig, "trigger": trig_id,
                 "plans": [p["plan_id"] for p in inc["plans"]], "material": material}, now.isoformat())
    return {"action": "opened" if inc["revision"] == 1 else "updated", "incident_id": inc["id"]}


def _inputs_snapshot(fleet: Fleet, asg) -> dict:
    ids = {asg.driver_id} | ({asg.relay.driver_id} if asg.relay else set())
    return {
        "assignment": asg.model_dump(mode="json"),
        "load": fleet.loads[asg.load_id].model_dump(mode="json"),
        "drivers": {d: fleet.drivers[d].model_dump(mode="json", include={"id", "eld", "position", "availability"}) for d in ids if d},
    }


def request_driver_confirmation(store: Store, driver_id: str, question: str, key: str, now: datetime, incident_id: str | None = None) -> bool:
    rid = store.outbox_add("driver", driver_id, question, "queued", incident_id, False, key, now.isoformat())
    if rid:
        store.audit("driver_status_requested", f"incident:{incident_id}" if incident_id else f"driver:{driver_id}",
                    {"driver_id": driver_id, "question": question}, now.isoformat())
    return rid is not None


# ---------------------------------------------------------------- approvals
def approve_plan(store: Store, clock: SimClock, plan_id: str, actor: str) -> dict:
    now = clock.now()
    got = store.get_plan(plan_id)
    if not got:
        raise ApprovalError(f"unknown plan {plan_id}")
    plan, status = got
    if status != "PROPOSED":
        raise ApprovalError(f"plan {plan_id} is {status}")
    if datetime.fromisoformat(plan["expires_at"]) <= now:
        raise ApprovalError(f"plan {plan_id} expired at {plan['expires_at']}; re-evaluate first")
    if not actor or actor.startswith("agent"):
        raise ApprovalError("approvals must come from a named human dispatcher")
    token = "apv_" + secrets.token_urlsafe(18)
    store.put_approval(token, plan_id, plan["incident_id"], actor, now.isoformat(), plan["expires_at"])
    store.set_plan_status(plan_id, "APPROVED")
    store.audit("plan_approved", f"incident:{plan['incident_id']}", {"plan_id": plan_id, "actor": actor, "title": plan["title"]}, now.isoformat())
    return {"plan_id": plan_id, "approval_token": token, "expires_at": plan["expires_at"], "actor": actor}


def reject_plan(store: Store, clock: SimClock, plan_id: str, actor: str, reason: str) -> dict:
    now = clock.now()
    got = store.get_plan(plan_id)
    if not got:
        raise ApprovalError(f"unknown plan {plan_id}")
    plan, _ = got
    store.set_plan_status(plan_id, "REJECTED")
    inc = store.get_incident(plan["incident_id"])
    inc["history"].append({"at": now.isoformat(), "what": f"{actor} rejected '{plan['title']}': {reason}"})
    for p in inc["plans"]:
        if p["plan_id"] == plan_id:
            p["rejected_by"] = actor
    store.put_incident(inc)
    store.audit("plan_rejected", f"incident:{plan['incident_id']}", {"plan_id": plan_id, "actor": actor, "reason": reason}, now.isoformat())
    return {"plan_id": plan_id, "status": "REJECTED"}


def execute_approved_plan(store: Store, clock: SimClock, plan_id: str, approval_token: str, idempotency_key: str) -> dict:
    if not idempotency_key:
        raise ApprovalError("idempotency_key is required")
    with store.lock:
        prior = store.get_execution(idempotency_key)
        if prior:
            return {**prior, "duplicate": True}
        now = clock.now()
        ap = store.get_approval(approval_token or "")
        if not ap or ap["plan_id"] != plan_id:
            raise ApprovalError("APPROVAL_REQUIRED: no valid dispatcher approval token for this plan")
        if ap["used_at"]:
            raise ApprovalError("approval token already used")
        if datetime.fromisoformat(ap["expires_at"]) <= now:
            raise ApprovalError("approval expired; re-evaluate and approve again")
        plan, status = store.get_plan(plan_id)
        if status != "APPROVED":
            raise ApprovalError(f"plan is {status}, not APPROVED")
        fleet = Fleet.load(store)
        asg = fleet.assignments[store.get_incident(plan["incident_id"])["assignment_id"]]

        # Deterministic revalidation against current state, immediately before commit.
        pb = Planner(fleet).build(asg, now, plan["actions"])
        ev = evaluate_plan(fleet, asg.id, pb, now)
        if ev["compliance"]["verdict"] != "PASS":
            store.audit("execution_blocked", f"incident:{plan['incident_id']}", {"plan_id": plan_id, "verdict": ev["compliance"]["verdict"],
                        "hard_failures": ev["compliance"]["hard_failures"], "unknowns": ev["compliance"]["unknowns"]}, now.isoformat())
            raise ApprovalError(f"revalidation failed ({ev['compliance']['verdict']}); plan not executed")

        changes = _apply_actions(fleet, store, asg, plan, now)
        store.use_approval(approval_token, now.isoformat())
        store.set_plan_status(plan_id, "EXECUTED")
        inc = store.get_incident(plan["incident_id"])
        for p in inc["plans"]:
            if p["plan_id"] != plan_id:
                store.set_plan_status(p["plan_id"], "SUPERSEDED")
        inc.update(status="EXECUTED", executed_plan_id=plan_id, updated_at=now.isoformat())
        inc["history"].append({"at": now.isoformat(), "what": f"Executed '{plan['title']}' approved by {ap['actor']}"})
        store.put_incident(inc)
        result = {"plan_id": plan_id, "incident_id": inc["id"], "executed_at": now.isoformat(), "approved_by": ap["actor"],
                  "changes": changes, "revalidation": {"verdict": ev["compliance"]["verdict"], "delivery_eta": ev["delivery_eta"],
                                                       "appointment_slack_minutes": ev["appointment_slack_minutes"]}}
        store.put_execution(idempotency_key, plan_id, now.isoformat(), result)
        store.audit("execution", f"incident:{inc['id']}", result, now.isoformat())
        store.audit("execution", f"assignment:{asg.id}", result, now.isoformat())
        fleet = Fleet.load(store)
        post = evaluate_assignment(fleet, asg.id, now)
        store.put_evaluation(asg.id, now.isoformat(), post)
        return result


def _apply_actions(fleet: Fleet, store: Store, asg, plan: dict, now: datetime) -> list[str]:
    changes = []
    load = fleet.loads[asg.load_id]
    for a in plan["actions"]:
        t = a["type"]
        if t == "RELAY":
            asg.relay = Relay(point_id=a["point_id"], driver_id=a["driver_id"], tractor_id=a.get("tractor_id"), mode=a.get("mode", "bobtail"))
            d = fleet.drivers[a["driver_id"]]
            d.availability, d.last_schedule_change_at = "assigned", now
            fleet.save(store, d)
            if a.get("tractor_id"):
                tr = fleet.tractors[a["tractor_id"]]
                tr.status = "in_use"
                fleet.save(store, tr)
            changes.append(f"{asg.id}: relay committed, {a['driver_id']} takes over at {a['point_id']}")
            store.outbox_add("driver", a["driver_id"], f"Relay assignment: meet {asg.driver_id} at {fleet.net.loc(a['point_id']).name} "
                             f"and take {asg.trailer_id} ({load.id}) to {fleet.net.loc(load.destination_id).name}.", "queued", plan["incident_id"],
                             False, f"dispatch:{plan['plan_id']}:{a['driver_id']}", now.isoformat())
            store.outbox_add("driver", asg.driver_id, f"Plan change: hand {asg.trailer_id} to {a['driver_id']} at {fleet.net.loc(a['point_id']).name}.",
                             "queued", plan["incident_id"], False, f"dispatch:{plan['plan_id']}:{asg.driver_id}", now.isoformat())
        elif t == "SWAP_DRIVER" and asg.progress.stage == "AT_PICKUP" and asg.driver_id and asg.status == "DISPATCHED":
            # Replacement must travel to the shipper first; the swap completes at the handoff.
            asg.committed = [a]
            new = fleet.drivers[a["driver_id"]]
            new.availability, new.last_schedule_change_at = "assigned", now
            fleet.save(store, new)
            changes.append(f"{asg.id}: {new.id} dispatched to the shipper to take over from {asg.driver_id}")
            store.outbox_add("driver", new.id, f"New assignment {load.id}: report to {fleet.net.loc(load.origin_id).name} to take over {asg.trailer_id}.",
                             "queued", plan["incident_id"], False, f"dispatch:{plan['plan_id']}:{new.id}", now.isoformat())
        elif t == "SWAP_DRIVER":
            old = fleet.drivers.get(asg.driver_id) if asg.driver_id else None
            if old:
                old.availability = "available"
                old.last_schedule_change_at = now
                fleet.save(store, old)
            new = fleet.drivers[a["driver_id"]]
            new.availability, new.last_schedule_change_at = "assigned", now
            fleet.save(store, new)
            asg.driver_id = new.id
            if a.get("mode") == "bobtail" and a.get("tractor_id"):
                asg.tractor_id = a["tractor_id"]
            if asg.status == "DECLINED":
                asg.status = "DISPATCHED" if fleet.loads[asg.load_id].status == "active" else "PROPOSED"
            changes.append(f"{asg.id}: driver changed {old.id if old else 'none'} -> {new.id}")
            store.outbox_add("driver", new.id, f"New assignment {load.id}: report to {fleet.net.loc(load.origin_id).name}.", "queued",
                             plan["incident_id"], False, f"dispatch:{plan['plan_id']}:{new.id}", now.isoformat())
        elif t == "HOLD_FOR_RESET":
            asg.hold = Hold(point_id=a["point_id"])
            changes.append(f"{asg.id}: {asg.driver_id} holds for a 10-hour reset at {a['point_id']}")
        elif t == "SWAP_TRACTOR":
            asg.tractor_id = a["tractor_id"]
            tr = fleet.tractors[a["tractor_id"]]
            tr.status = "in_use"
            fleet.save(store, tr)
            changes.append(f"{asg.id}: repowered with {a['tractor_id']}")
        elif t == "CONTINUE":
            changes.append(f"{asg.id}: plan unchanged")
            if plan.get("service_tier") == "LATE":
                draft_customer_message(store, fleet, plan["incident_id"], plan["plan_id"], now)
                changes.append(f"{load.id}: customer delay notice drafted (sending needs approval)")
        elif t == "REQUEST_NEW_APPOINTMENT":
            body = (f"{fleet.customers[load.customer_id].name}: load {load.id} will deliver late. We request a new appointment "
                    f"{_clock(a['start'])} to {_clock(a['end'])}.")
            store.outbox_add("customer", load.customer_id, body, "approved_to_send", plan["incident_id"], True,
                             f"appt:{plan['plan_id']}", now.isoformat())
            changes.append(f"{load.id}: appointment change requested (pending customer confirmation)")
    asg.revision += 1
    asg.history.append({"at": now.isoformat(), "plan_id": plan["plan_id"], "changes": changes})
    fleet.save(store, asg)
    return changes


def draft_customer_message(store: Store, fleet: Fleet, incident_id: str, plan_id: str, now: datetime) -> dict:
    inc = store.get_incident(incident_id)
    plan = next((p for p in inc["plans"] if p["plan_id"] == plan_id), None)
    if not plan:
        return {"error": f"plan {plan_id} not on incident {incident_id}"}
    load = fleet.loads[inc["load_id"]]
    cust = fleet.customers[load.customer_id]
    s = plan["appointment_slack_minutes"]
    if s is not None and s >= 0:
        body = (f"Hello {cust.name} team, load {load.id} ({load.commodity}) remains on schedule for your "
                f"{_clock(load.delivery_window.start)} to {_clock(load.delivery_window.end)} window; current ETA {_clock(plan['delivery_eta'])}.")
    else:
        body = (f"Hello {cust.name} team, load {load.id} ({load.commodity}) is delayed after a pickup delay at origin. "
                f"Current ETA {_clock(plan['delivery_eta'])}. We will confirm any appointment change before committing.")
    rid = store.outbox_add("customer", load.customer_id, body, "draft", incident_id, True, f"draft:{plan_id}:{secrets.token_hex(4)}", now.isoformat())
    store.audit("customer_message_drafted", f"incident:{incident_id}", {"plan_id": plan_id, "outbox_id": rid}, now.isoformat())
    return {"outbox_id": rid, "status": "draft", "requires_approval": True, "body": body}


def record_override(store: Store, clock: SimClock, incident_id: str, actor: str, reason: str, idempotency_key: str) -> dict:
    now = clock.now()
    inc = store.get_incident(incident_id)
    if inc is None:
        raise ApprovalError(f"unknown incident {incident_id}")
    if not actor or actor.startswith("agent"):
        raise ApprovalError("overrides must be recorded by a named human")
    if inc["evaluation"]["hard_failures"]:
        raise ApprovalError("PROHIBITED: a hard compliance failure cannot be overridden")
    prior = store.get_execution(idempotency_key)
    if prior:
        return {**prior, "duplicate": True}
    inc.update(status="OVERRIDDEN", updated_at=now.isoformat())
    inc["history"].append({"at": now.isoformat(), "what": f"Override by {actor}: {reason}"})
    store.put_incident(inc)
    result = {"incident_id": incident_id, "actor": actor, "reason": reason, "at": now.isoformat()}
    store.put_execution(idempotency_key, f"override:{incident_id}", now.isoformat(), result)
    store.audit("override", f"incident:{incident_id}", result, now.isoformat())
    return result
