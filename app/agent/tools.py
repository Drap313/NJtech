"""Narrow typed tools for the agent. Tools own facts, arithmetic, optimization
and writes; the model only chooses tools and explains results.

Gate: every mutating tool needs an idempotency key, and every tool that changes
an assignment or makes an external commitment needs a dispatcher approval token.
The agent has no way to mint approval tokens.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ..audit.trail import decision_trace
from ..clock import SimClock, minutes_between, utc
from ..db import Store
from ..forecasting.evaluator import evaluate_assignment
from ..forecasting.risk import score_schedule_risk
from ..incidents import service
from ..optimization.solver import find_costed_alternatives
from ..state import Fleet


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]
    mutating: bool = False
    needs_approval: bool = False


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S = {"type": "string"}
I = {"type": "integer"}


class ToolBox:
    def __init__(self, store: Store, clock: SimClock):
        self.store, self.clock = store, clock
        self.tools: dict[str, Tool] = {t.name: t for t in self._define()}

    # ------------------------------------------------------------------ registry
    def specs(self) -> list[dict]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in self.tools.values()]

    def call(self, name: str, args: dict, actor: str = "agent") -> dict:
        t = self.tools.get(name)
        if t is None:
            return {"error": f"unknown tool {name}"}
        if t.mutating and not args.get("idempotency_key"):
            return {"error": "IDEMPOTENCY_KEY_REQUIRED", "detail": f"{name} mutates state and needs an idempotency_key"}
        if t.needs_approval and not args.get("approval_token"):
            self.store.audit("agent_blocked", f"tool:{name}", {"args": args, "actor": actor}, self.clock.now().isoformat())
            return {"error": "APPROVAL_REQUIRED", "detail": f"{name} requires a dispatcher approval token. Ask the dispatcher to approve the plan in the dashboard."}
        try:
            out = t.fn(**args)
        except service.ApprovalError as e:
            self.store.audit("agent_blocked", f"tool:{name}", {"args": {k: v for k, v in args.items() if k != "approval_token"}, "error": str(e)},
                             self.clock.now().isoformat())
            return {"error": "APPROVAL_REQUIRED" if "APPROVAL" in str(e) else "REJECTED", "detail": str(e)}
        except TypeError as e:
            return {"error": "BAD_ARGUMENTS", "detail": str(e)}
        except KeyError as e:
            return {"error": "NOT_FOUND", "detail": f"unknown id {e}"}
        if t.mutating:
            self.store.audit("agent_tool_write", f"tool:{name}", {"args": {k: v for k, v in args.items() if k != "approval_token"}, "actor": actor},
                             self.clock.now().isoformat())
        return out

    # ------------------------------------------------------------------ helpers
    def _fleet(self) -> Fleet:
        return Fleet.load(self.store)

    def _now(self) -> datetime:
        return self.clock.now()

    def _asg_id(self, fleet: Fleet, ref: str) -> str:
        if ref in fleet.assignments:
            return ref
        for a in sorted(fleet.assignments.values(), key=lambda a: a.id):
            if a.load_id == ref and a.status not in ("CANCELLED", "COMPLETED"):
                return a.id
        raise KeyError(ref)

    # ------------------------------------------------------------------ tools
    def _define(self) -> list[Tool]:
        return [
            Tool("get_fleet_state", "Current drivers, equipment, loads and assignments with latest verdicts.", _obj({}, []), self.get_fleet_state),
            Tool("get_driver_status", "One driver's ELD clocks, location, qualifications and freshness.", _obj({"driver_id": S}, ["driver_id"]), self.get_driver_status),
            Tool("get_load_status", "One load's assignment, stage, windows and latest evaluation summary.", _obj({"load_id": S}, ["load_id"]), self.get_load_status),
            Tool("evaluate_assignment", "Deterministic legality + feasibility evaluation of an assignment (or load id) now.",
                 _obj({"assignment_id": S}, ["assignment_id"]), self.evaluate),
            Tool("check_hos_and_qualifications", "Compliance verdict and rule trace for an assignment's current plan.",
                 _obj({"assignment_id": S}, ["assignment_id"]), self.check_hos),
            Tool("forecast_active_load", "Timeline forecast (stops, ETAs) for an assignment.", _obj({"assignment_id": S}, ["assignment_id"]), self.forecast),
            Tool("score_schedule_risk", "Schedule-risk points with evidence for a driver on their current plan. Not a fatigue assessment.",
                 _obj({"driver_id": S}, ["driver_id"]), self.risk),
            Tool("list_open_incidents", "Open incidents with severity and recommendation.", _obj({}, []), self.list_open_incidents),
            Tool("get_incident", "Full incident: cause, impact, ranked plans with costs, rejected options.", _obj({"incident_id": S}, ["incident_id"]), self.get_incident),
            Tool("find_costed_alternatives", "Re-run the recovery solver for an incident's assignment.",
                 _obj({"incident_id": S, "max_options": I}, ["incident_id"]), self.alternatives),
            Tool("get_decision_trace", "Persisted inputs, policy versions and audit trail behind an incident.", _obj({"incident_id": S}, ["incident_id"]), self.trace),
            Tool("draft_customer_message", "Draft (not send) a customer update for a plan. Sending needs approval.",
                 _obj({"incident_id": S, "plan_id": S}, ["incident_id", "plan_id"]), self.draft_customer_message),
            Tool("request_driver_confirmation", "Ask a driver to confirm status/location. Allowed automatically.",
                 _obj({"driver_id": S, "question": S, "idempotency_key": S}, ["driver_id", "question", "idempotency_key"]),
                 self.request_driver_confirmation, mutating=True),
            Tool("create_dispatch_incident", "Open an internal incident by re-evaluating an assignment now.",
                 _obj({"assignment_id": S, "idempotency_key": S}, ["assignment_id", "idempotency_key"]), self.create_incident, mutating=True),
            Tool("execute_approved_plan", "Execute a plan a dispatcher already approved. Requires the dispatcher's approval token.",
                 _obj({"plan_id": S, "approval_token": S, "idempotency_key": S}, ["plan_id", "approval_token", "idempotency_key"]),
                 self.execute, mutating=True, needs_approval=True),
            Tool("record_override", "Record a human override on an incident. Requires an approval token; hard compliance failures cannot be overridden.",
                 _obj({"incident_id": S, "actor": S, "reason": S, "approval_token": S, "idempotency_key": S},
                      ["incident_id", "actor", "reason", "approval_token", "idempotency_key"]), self.override, mutating=True, needs_approval=True),
        ]

    def get_fleet_state(self) -> dict:
        f, now = self._fleet(), self._now()
        drivers = []
        for d in f.drivers.values():
            drivers.append({"id": d.id, "name": d.name, "availability": d.availability, "duty_status": d.eld.duty_status.value,
                            "location": d.position.location_id or f"{d.position.lat:.2f},{d.position.lon:.2f}",
                            "eld_age_min": round(minutes_between(utc(d.eld.observed_at), now)), "endorsements": d.endorsements,
                            "tractor": d.tractor_id})
        asgs = []
        for a in sorted(f.assignments.values(), key=lambda a: a.id):
            ev = self.store.get_evaluation(a.id) or {}
            asgs.append({"id": a.id, "load": a.load_id, "status": a.status, "driver": a.driver_id, "stage": a.progress.stage,
                         "verdict": ev.get("verdict"), "delivery_eta": ev.get("delivery_eta"), "slack_min": ev.get("appointment_slack_minutes"),
                         "relay": a.relay.model_dump() if a.relay else None})
        return {"now": now.isoformat(), "drivers": drivers, "assignments": asgs,
                "tractors": [{"id": t.id, "status": t.status} for t in f.tractors.values()],
                "trailers": [{"id": t.id, "type": t.type, "status": t.status} for t in f.trailers.values()]}

    def get_driver_status(self, driver_id: str) -> dict:
        f, now = self._fleet(), self._now()
        d = f.drivers[driver_id]
        out = d.model_dump(mode="json", exclude={"recent_shifts"})
        out["eld_age_minutes"] = round(minutes_between(utc(d.eld.observed_at), now))
        a = f.assignment_for_driver(driver_id)
        out["active_assignment"] = a.id if a else None
        return out

    def get_load_status(self, load_id: str) -> dict:
        f = self._fleet()
        load = f.loads[load_id]
        aid = self._asg_id(f, load_id)
        ev = self.store.get_evaluation(aid) or {}
        return {"load": load.model_dump(mode="json"), "assignment": f.assignments[aid].model_dump(mode="json", exclude={"history"}),
                "latest_evaluation": {k: ev.get(k) for k in ("verdict", "plan_title", "delivery_eta", "appointment_slack_minutes", "hos_reserve_minutes", "at")}}

    def evaluate(self, assignment_id: str) -> dict:
        f = self._fleet()
        ev = evaluate_assignment(f, self._asg_id(f, assignment_id), self._now())
        ev.pop("timeline", None)
        ev["compliance"].pop("rule_trace", None)
        return ev

    def check_hos(self, assignment_id: str) -> dict:
        f = self._fleet()
        return evaluate_assignment(f, self._asg_id(f, assignment_id), self._now())["compliance"]

    def forecast(self, assignment_id: str) -> dict:
        f = self._fleet()
        ev = evaluate_assignment(f, self._asg_id(f, assignment_id), self._now())
        return {"timeline": [{k: e[k] for k in ("driver_id", "start", "end", "status", "label")} for e in ev["timeline"]],
                "pickup_eta": ev["pickup_eta"], "delivery_eta": ev["delivery_eta"], "appointment_slack_minutes": ev["appointment_slack_minutes"],
                "hos_reserve_minutes": ev["hos_reserve_minutes"], "required_stops": ev["required_stops"]}

    def risk(self, driver_id: str) -> dict:
        f, now = self._fleet(), self._now()
        a = f.assignment_for_driver(driver_id)
        sim = None
        if a:
            from ..forecasting.planner import Planner
            pb = Planner(f).build(a, now)
            sim = next((l.sim for l in pb.legs if l.driver.id == driver_id), None)
        return score_schedule_risk(f.drivers[driver_id], sim, now, f.net)

    def list_open_incidents(self) -> list[dict]:
        out = []
        for i in self.store.list_incidents("OPEN"):
            rec = i["plans"][0] if i.get("plans") else None
            out.append({"id": i["id"], "severity": i["severity"], "load": i["load_id"], "title": i["title"], "cause": i["cause"],
                        "recommended": rec and {"plan_id": rec["plan_id"], "title": rec["title"], "incremental_cost": rec["incremental_cost"]}})
        return out

    def get_incident(self, incident_id: str) -> dict:
        i = self.store.get_incident(incident_id)
        if not i:
            raise KeyError(incident_id)
        slim = {k: i[k] for k in ("id", "status", "severity", "title", "cause", "customer_impact", "summary", "load_id", "assignment_id", "rejected", "also_considered")}
        slim["current_plan_verdict"] = i["evaluation"]["verdict"]
        slim["current_plan_failures"] = i["evaluation"]["hard_failures"]
        slim["plans"] = [{k: p[k] for k in ("plan_id", "title", "kind", "compliance_verdict", "service_tier", "delivery_eta",
                                             "appointment_slack_minutes", "hos_reserve_minutes", "incremental_cost", "cost_breakdown",
                                             "risk_score", "risk_band", "assumptions", "expires_at", "required_approvals", "recheck")}
                         for p in i.get("plans", [])]
        return slim

    def alternatives(self, incident_id: str, max_options: int = 5) -> dict:
        i = self.store.get_incident(incident_id)
        r = find_costed_alternatives(self._fleet(), i["assignment_id"], self._now(), max_options)
        for p in r["plans"]:
            for k in ("timeline", "rule_trace", "required_stops"):
                p.pop(k, None)
        r.pop("current_plan", None)
        return r

    def trace(self, incident_id: str) -> dict:
        t = decision_trace(self.store, incident_id)
        t.pop("inputs_snapshot", None)
        return t

    def draft_customer_message(self, incident_id: str, plan_id: str) -> dict:
        return service.draft_customer_message(self.store, self._fleet(), incident_id, plan_id, self._now())

    def request_driver_confirmation(self, driver_id: str, question: str, idempotency_key: str) -> dict:
        ok = service.request_driver_confirmation(self.store, driver_id, question, idempotency_key, self._now())
        return {"queued": ok, "duplicate": not ok}

    def create_incident(self, assignment_id: str, idempotency_key: str) -> dict:
        from ..events.processor import Engine, make_event
        eng = Engine(self.store, self.clock)
        f = self._fleet()
        ev = make_event("REFORECAST", "assignment", self._asg_id(f, assignment_id), {"reason": "agent request"}, "agent", self._now(),
                        idempotency_key=idempotency_key)
        return eng.process_event(ev)

    def execute(self, plan_id: str, approval_token: str, idempotency_key: str) -> dict:
        return service.execute_approved_plan(self.store, self.clock, plan_id, approval_token, idempotency_key)

    def override(self, incident_id: str, actor: str, reason: str, approval_token: str, idempotency_key: str) -> dict:
        ap = self.store.get_approval(approval_token)
        if not ap or ap["plan_id"] != f"override:{incident_id}" or ap["used_at"]:
            raise service.ApprovalError("APPROVAL_REQUIRED: no valid override approval token for this incident")
        self.store.use_approval(approval_token, self._now().isoformat())
        return service.record_override(self.store, self.clock, incident_id, actor, reason, idempotency_key)


_ET = ZoneInfo("America/New_York")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:\d{2}|Z)")


def _localize(obj: Any) -> Any:
    """Render timestamps as dispatcher-local time so the model never has to convert UTC."""
    if isinstance(obj, dict):
        return {k: _localize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_localize(v) for v in obj]
    if isinstance(obj, str) and _ISO.search(obj):
        return _ISO.sub(lambda m: datetime.fromisoformat(m.group(0).replace("Z", "+00:00")).astimezone(_ET).strftime("%a %H:%M %Z"), obj)
    return obj


def compact(obj: Any, limit: int = 6000) -> str:
    s = json.dumps(_localize(json.loads(json.dumps(obj, default=str))))
    return s if len(s) <= limit else s[:limit] + '..."(truncated)"'
