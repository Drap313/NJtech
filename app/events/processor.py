"""Event ingestion and reevaluation. Works without any LLM."""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta

from ..clock import SimClock, utc
from ..db import Store
from ..forecasting.evaluator import evaluate_assignment
from ..incidents.service import reconcile
from ..models import Assignment, EldSnapshot, Event, Load, Position, Window
from ..state import Fleet

TELEMETRY = ("ELD_UPDATED", "GPS_UPDATED", "REFORECAST")


def make_event(type: str, entity_type: str, entity_id: str, payload: dict, source: str, occurred_at: datetime,
               received_at: datetime | None = None, idempotency_key: str | None = None) -> Event:
    eid = "EV-" + uuid.uuid4().hex[:12]
    return Event(event_id=eid, type=type, entity_type=entity_type, entity_id=entity_id, occurred_at=occurred_at,
                 received_at=received_at or occurred_at, source=source, payload=payload, idempotency_key=idempotency_key or eid)


class Engine:
    def __init__(self, store: Store, clock: SimClock):
        self.store = store
        self.clock = clock

    # ------------------------------------------------------------------ main entry
    def process_event(self, event: Event | dict) -> dict:
        t_start = time.perf_counter()
        ev = event if isinstance(event, Event) else Event.model_validate(event)
        with self.store.lock:
            if not self.store.insert_event(ev.model_dump(mode="json")):
                prior = self.store.event_by_key(ev.idempotency_key)
                return {"event_id": prior["event_id"] if prior else ev.event_id, "status": "duplicate", "affected": []}
            now = max(self.clock.now(), utc(ev.occurred_at)) if ev.type in TELEMETRY else self.clock.now()
            fleet = Fleet.load(self.store)
            try:
                affected = self._apply(fleet, ev, now)
            except (KeyError, ValueError) as e:
                result = {"event_id": ev.event_id, "type": ev.type, "status": "rejected", "error": str(e), "affected": []}
                self.store.set_event_result(ev.event_id, result)
                return result
            fleet = Fleet.load(self.store)
            out = [self._reevaluate(fleet, aid, now, ev) for aid in sorted(set(affected))]
            result = {"event_id": ev.event_id, "type": ev.type, "status": "processed", "affected": out,
                      "processing_ms": round((time.perf_counter() - t_start) * 1000, 1)}
            self.store.set_event_result(ev.event_id, result)
            if ev.type not in TELEMETRY:
                self.store.audit("event_processed", f"event:{ev.event_id}", result, now.isoformat())
            return result

    def sweep(self, reason: str = "REFORECAST") -> list[dict]:
        """Re-evaluate every active and proposed assignment."""
        with self.store.lock:
            now = self.clock.now()
            fleet = Fleet.load(self.store)
            ids = [a.id for a in sorted(fleet.assignments.values(), key=lambda a: a.id)
                   if a.status in ("DISPATCHED", "PROPOSED", "BLOCKED", "DECLINED")]
            return [self._reevaluate(fleet, aid, now, None) for aid in ids]

    # ------------------------------------------------------------------ evaluation
    def _reevaluate(self, fleet: Fleet, aid: str, now: datetime, ev: Event | None) -> dict:
        asg = fleet.assignments[aid]
        if asg.status in ("COMPLETED", "CANCELLED"):
            return {"assignment_id": aid, "status": asg.status}
        if asg.status == "DECLINED" or asg.driver_id is None:
            evaluation = None
            verdict, tier = "NO_DRIVER", None
        else:
            evaluation = evaluate_assignment(fleet, aid, now)
            self.store.put_evaluation(aid, now.isoformat(), evaluation)
            verdict, tier = evaluation["verdict"], evaluation["feasibility"]["service_tier"]
        res = {"assignment_id": aid, "load_id": asg.load_id, "verdict": verdict, "service_tier": tier,
               "delivery_eta": evaluation and evaluation["delivery_eta"],
               "appointment_slack_minutes": evaluation and evaluation["appointment_slack_minutes"],
               "hos_reserve_minutes": evaluation and evaluation["hos_reserve_minutes"]}
        if asg.status in ("PROPOSED", "BLOCKED"):
            # Pre-dispatch gate: an invalid or unqualified assignment cannot be released.
            new = "BLOCKED" if verdict in ("FAIL", "UNKNOWN") else "PROPOSED"
            if new != asg.status:
                asg.status = new
                fleet.save(self.store, asg)
                self.store.audit("predispatch_gate", f"assignment:{aid}", {"status": new, "verdict": verdict,
                                 "hard_failures": evaluation["compliance"]["hard_failures"] if evaluation else []}, now.isoformat())
            res["release_status"] = new
            return res
        trigger = ev.model_dump(mode="json") if ev else None
        if evaluation is None:
            evaluation = _declined_stub(fleet, asg, now)
        res["incident"] = reconcile(self.store, fleet, asg, evaluation, now, trigger)
        return res

    # ------------------------------------------------------------------ state mutation
    def _apply(self, fleet: Fleet, ev: Event, now: datetime) -> list[str]:
        p, t, at = ev.payload, ev.type, utc(ev.occurred_at)
        S = self.store

        def asg_for_load(load_id: str) -> Assignment:
            cands = [a for a in fleet.assignments_for_load(load_id) if a.status in ("DISPATCHED", "PROPOSED", "BLOCKED", "DECLINED")]
            if not cands:
                raise KeyError(f"no open assignment for load {load_id}")
            return cands[0]

        def target_asg() -> Assignment:
            if ev.entity_type == "assignment":
                return fleet.assignments[ev.entity_id]
            if ev.entity_type == "load":
                return asg_for_load(ev.entity_id)
            if ev.entity_type == "driver":
                a = fleet.assignment_for_driver(ev.entity_id)
                if not a:
                    raise KeyError(f"driver {ev.entity_id} has no active assignment")
                return a
            raise KeyError(f"cannot resolve assignment from {ev.entity_type}")

        def note(a: Assignment, what: str):
            a.history.append({"at": at.isoformat(), "event_id": ev.event_id, "what": what})
            fleet.save(S, a)

        if t == "PICKUP_DELAYED":
            a = target_asg()
            if a.progress.stage not in ("PLANNED", "AT_PICKUP"):
                raise ValueError(f"{a.load_id} already left pickup ({a.progress.stage.lower()}); report an en-route delay instead")
            a.pickup_delay_minutes += int(p["delay_minutes"])
            note(a, f"pickup delayed +{p['delay_minutes']} min: {p.get('reason', '')}")
            return [a.id]
        if t == "DRIVER_DELAY_REPORTED":
            a = target_asg()
            d = int(p["delay_minutes"])
            if a.progress.stage in ("PLANNED", "AT_PICKUP"):
                a.pickup_delay_minutes += d
            else:
                base = max(at, utc(a.enroute_delay_until)) if a.enroute_delay_until else at
                a.enroute_delay_until = base + timedelta(minutes=d)
            note(a, f"driver reported delay +{d} min: {p.get('reason', '')}")
            return [a.id]
        if t == "ROUTE_TIME_CHANGED":
            a = target_asg()
            a.route_extra_minutes += int(p["extra_minutes"])
            note(a, f"route time {p['extra_minutes']:+d} min: {p.get('reason', '')}")
            return [a.id]
        if t == "DELIVERY_WINDOW_CHANGED":
            a = target_asg()
            load = fleet.loads[a.load_id]
            load.delivery_window = Window(start=p["start"], end=p["end"])
            fleet.save(S, load)
            note(a, f"delivery window changed to {p['start']} - {p['end']}")
            return [a.id]
        if t in ("VEHICLE_UNAVAILABLE", "TRAILER_UNAVAILABLE"):
            unit = (fleet.tractors if t == "VEHICLE_UNAVAILABLE" else fleet.trailers)[ev.entity_id]
            unit.status = "out_of_service"
            unit.out_of_service_until = datetime.fromisoformat(p["until"]) if p.get("until") else None
            fleet.save(S, unit)
            hit = [a.id for a in fleet.assignments.values() if a.status in ("DISPATCHED", "PROPOSED", "BLOCKED")
                   and ev.entity_id in (a.tractor_id, a.trailer_id, a.relay.tractor_id if a.relay else None)]
            return hit
        if t == "DRIVER_DECLINED":
            a = target_asg()
            who = p.get("driver_id") or a.driver_id
            a.declined_by.append(who)
            a.driver_id = None
            a.status = "DECLINED"
            note(a, f"{who} declined: {p.get('reason', '')}")
            return [a.id]
        if t == "DRIVER_ACCEPTED":
            a = target_asg()
            a.driver_confirmed_at = at
            d = fleet.drivers[a.driver_id]
            d.last_confirmation_at = at
            fleet.save(S, d)
            note(a, f"{d.id} confirmed")
            return [a.id]
        if t == "ASSIGNMENT_APPROVED":
            a = fleet.assignments[ev.entity_id]
            last = S.get_evaluation(a.id)
            if a.status == "BLOCKED" or (last and last["verdict"] != "PASS"):
                raise ValueError(f"release blocked: {a.id} verdict {last['verdict'] if last else 'unevaluated'}")
            a.status = "DISPATCHED"
            fleet.loads[a.load_id].status = "active"
            fleet.save(S, fleet.loads[a.load_id])
            note(a, "released to dispatch")
            return [a.id]
        if t == "ASSIGNMENT_PROPOSED":
            a = Assignment.model_validate({"status": "PROPOSED", **p})
            fleet.save(S, a)
            return [a.id]
        if t == "LOAD_CREATED":
            fleet.save(S, Load.model_validate({"status": "proposed", **p}))
            return []
        if t == "CUSTOMER_MESSAGE_RECEIVED":
            a = target_asg()
            load = fleet.loads[a.load_id]
            load.customer_messages.append({"at": at.isoformat(), "source": ev.source, "text": p.get("text", "")})
            fleet.save(S, load)
            return [a.id]
        if t == "DRIVER_STATUS_CHANGED":
            d = fleet.drivers[ev.entity_id]
            d.eld = d.eld.model_copy(update={"duty_status": p["duty_status"], "status_since": at, "observed_at": at, "source": ev.source})
            fleet.save(S, d)
            a = fleet.assignment_for_driver(d.id)
            return [a.id] if a else []
        if t in ("ELD_UPDATED", "GPS_UPDATED"):
            d = fleet.drivers[ev.entity_id]
            if "eld" in p:
                d.eld = EldSnapshot.model_validate(p["eld"])
                d.stale_flagged_at = None
            if "position" in p:
                d.position = Position.model_validate(p["position"])
                if d.tractor_id and d.tractor_id in fleet.tractors and fleet.tractors[d.tractor_id].status == "in_use":
                    tr = fleet.tractors[d.tractor_id]
                    tr.position = d.position
                    fleet.save(S, tr)
            fleet.save(S, d)
            a = fleet.assignment_for_driver(d.id)
            upd = p.get("assignment_update")
            if a and upd:
                if "route_minutes_done" in upd and a.progress.stage == "IN_TRANSIT":
                    a.progress.route_minutes_done = int(upd["route_minutes_done"])
                    a.progress.as_of = at
                if upd.get("relay_handoff_started_at") and a.relay and not a.relay.handoff_started_at:
                    a.relay.handoff_started_at = datetime.fromisoformat(upd["relay_handoff_started_at"])
                    a.history.append({"at": at.isoformat(), "what": f"relay handoff started at {a.relay.point_id}"})
                if upd.get("complete_swap") and a.committed:
                    act = a.committed[0]
                    old = fleet.drivers[a.driver_id]
                    old.availability = "available"
                    fleet.save(S, old)
                    if act.get("mode") == "bobtail" and act.get("tractor_id"):
                        a.tractor_id = act["tractor_id"]
                    a.driver_id, a.committed = act["driver_id"], None
                    a.history.append({"at": at.isoformat(), "what": f"driver swap completed; {a.driver_id} has the load"})
                if upd.get("complete_relay") and a.relay:
                    old = fleet.drivers[a.driver_id]
                    old.availability = "available"
                    fleet.save(S, old)
                    if a.relay.mode == "bobtail" and a.relay.tractor_id:
                        a.tractor_id = a.relay.tractor_id
                    a.driver_id, a.relay = a.relay.driver_id, None
                    a.history.append({"at": at.isoformat(), "what": f"relay completed; {a.driver_id} now has the load"})
                if upd.get("clear_hold") and a.hold:
                    a.hold = None
                    a.history.append({"at": at.isoformat(), "what": "reset complete; driver back on the road"})
                fleet.save(S, a)
            return [a.id] if a else []
        if t in ("PICKUP_STARTED", "PICKUP_COMPLETED", "ARRIVED_AT_DELIVERY", "DELIVERY_COMPLETED"):
            a = target_asg()
            stage = {"PICKUP_STARTED": "AT_PICKUP", "PICKUP_COMPLETED": "IN_TRANSIT", "ARRIVED_AT_DELIVERY": "AT_DELIVERY",
                     "DELIVERY_COMPLETED": "DELIVERED"}[t]
            a.progress.stage, a.progress.stage_started_at, a.progress.as_of = stage, at, at
            if stage == "IN_TRANSIT":
                a.progress.route_minutes_done = 0
            if stage == "DELIVERED":
                a.status = "COMPLETED"
                load = fleet.loads[a.load_id]
                load.status = "delivered"
                fleet.save(S, load)
                for did in {a.driver_id}:
                    if did:
                        drv = fleet.drivers[did]
                        drv.availability = "available"
                        fleet.save(S, drv)
            note(a, t.lower().replace("_", " "))
            return [a.id]
        if t == "DATA_BECAME_STALE":
            d = fleet.drivers[ev.entity_id]
            d.stale_flagged_at = at
            fleet.save(S, d)
            a = fleet.assignment_for_driver(d.id)
            return [a.id] if a else []
        if t == "REFORECAST":
            if ev.entity_type == "assignment":
                return [ev.entity_id]
            return [a.id for a in fleet.active_assignments()]
        raise ValueError(f"unhandled event type {t}")


def _declined_stub(fleet: Fleet, asg: Assignment, now: datetime) -> dict:
    """Minimal evaluation for an assignment that has no driver."""
    load = fleet.loads[asg.load_id]
    return {"assignment_id": asg.id, "load_id": load.id, "verdict": "UNKNOWN", "plan_title": "No driver",
            "compliance": {"verdict": "UNKNOWN", "hard_failures": [], "warnings": [], "manual_review": [],
                           "unknowns": [{"code": "NO_DRIVER", "detail": "assignment declined; no driver"}], "rule_trace": []},
            "feasibility": {"service_tier": "UNKNOWN", "hard_failures": [], "warnings": [], "first_failure": None},
            "pickup_eta": None, "depart_eta": None, "delivery_eta": None, "appointment_slack_minutes": None,
            "hos_reserve_minutes": None, "delivery_window": load.delivery_window.model_dump(mode="json"), "risk": None,
            "timeline": [], "at": now.isoformat()}
