"""HTTP API + dashboard. Run: uvicorn app.api.main:app --host 0.0.0.0 --port 8090"""
from __future__ import annotations

import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..agent.intake import document_to_event, text_to_event
from ..agent.llm import LocalLLM, LocalVision
from ..agent.runner import explain_incident, run_agent
from ..agent.tools import ToolBox
from ..audit.trail import attach_jsonl, decision_trace
from ..clock import SimClock, minutes_between, utc
from ..compliance.hos import simulate
from ..config import AUDIT_JSONL, CONFIG_DIR, DB_PATH, SYNTHETIC_DIR, get_rules, load_yaml, policy_versions
from ..db import Store
from ..events.processor import Engine, make_event
from ..events.runtime import FleetRuntime
from ..forecasting.evaluator import evaluate_assignment
from ..forecasting.planner import Planner
from ..forecasting.risk import score_schedule_risk
from ..incidents import service
from ..seed import seed
from ..state import Fleet

STATIC = Path(__file__).parent / "static"


class Ctx:
    def __init__(self) -> None:
        self.store = Store(DB_PATH)
        attach_jsonl(self.store, AUDIT_JSONL)
        self.clock = SimClock(self.store)
        self.engine = Engine(self.store, self.clock)
        self.runtime = FleetRuntime(self.engine)
        self.tools = ToolBox(self.store, self.clock)
        self.llm = LocalLLM()
        self.vlm = LocalVision()
        self._health: dict = {}
        self._health_at = 0.0
        self.explaining: set[str] = set()

    def reset(self, speed: float | None = None) -> None:
        with self.store.lock:
            start = seed(self.store)
            self.clock.set(start, speed=speed if speed is not None else float(os.environ.get("DG_SPEED", "1")), paused=False)
            self.engine.sweep("SEED")

    def health(self) -> dict:
        if time.time() - self._health_at > 20:
            self._health = {"llm": self.llm.health(), "vlm": self.vlm.health()}
            self._health_at = time.time()
        return self._health


ctx: Ctx | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ctx
    ctx = Ctx()
    if ctx.store.meta_get("scenario_start") is None:
        ctx.reset()
    if os.environ.get("DG_RUNTIME", "1") != "0":
        ctx.runtime.start()
    yield
    ctx.runtime.stop()


app = FastAPI(title="Dispatch Guardian", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def C() -> Ctx:
    assert ctx is not None
    return ctx


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---------------------------------------------------------------- read models
def _driver_row(d, fleet: Fleet, now: datetime) -> dict:
    R = get_rules()
    res = simulate(d.eld, [], R, R.cycle_limit(d.cycle_profile), start=now)
    c = res.end
    win_left = round(minutes_between(now, c.shift_start + timedelta(minutes=R.window))) if c.shift_start else R.window
    age = minutes_between(utc(d.eld.observed_at), now)
    a = fleet.assignment_for_driver(d.id)
    loc = fleet.net.loc(d.position.location_id).name if d.position.location_id else "en route"
    return {"id": d.id, "name": d.name, "availability": d.availability, "duty_status": d.eld.duty_status.value,
            "status_since": d.eld.status_since.isoformat(), "location": loc, "lat": d.position.lat, "lon": d.position.lon,
            "eld_age_min": round(age), "stale": age > R.max_eld_age, "feed": d.feed,
            "drive_left": R.drive_limit - c.drive, "window_left": win_left, "cycle_left": R.cycle_limit(d.cycle_profile) - c.cycle,
            "endorsements": d.endorsements, "tractor_id": d.tractor_id, "assignment_id": a.id if a else None, "home": d.home_terminal}


def _asg_row(a, fleet: Fleet, store: Store) -> dict:
    load = fleet.loads[a.load_id]
    ev = store.get_evaluation(a.id) or {}
    route = fleet.net.routes[load.route_id]
    return {"id": a.id, "load_id": load.id, "route_id": load.route_id, "status": a.status, "driver_id": a.driver_id, "tractor_id": a.tractor_id,
            "trailer_id": a.trailer_id, "relay": a.relay.model_dump() if a.relay else None, "hold": a.hold.model_dump() if a.hold else None,
            "stage": a.progress.stage, "route_done": a.progress.route_minutes_done, "route_total": route.total_minutes,
            "customer": fleet.customers[load.customer_id].name, "origin": fleet.net.loc(load.origin_id).name,
            "destination": fleet.net.loc(load.destination_id).name, "equipment": load.equipment_type, "priority": load.priority,
            "pickup_delay": a.pickup_delay_minutes, "delivery_window": load.delivery_window.model_dump(mode="json"),
            "verdict": ev.get("verdict"), "tier": (ev.get("feasibility") or {}).get("service_tier"), "delivery_eta": ev.get("delivery_eta"),
            "slack": ev.get("appointment_slack_minutes"), "reserve": ev.get("hos_reserve_minutes"),
            "risk": (ev.get("risk") or {}).get("score"), "risk_band": (ev.get("risk") or {}).get("band"),
            "failures": [h["code"] for h in (ev.get("compliance") or {}).get("hard_failures", [])]
                        + [u["code"] for u in (ev.get("compliance") or {}).get("unknowns", [])],
            "plan_title": ev.get("plan_title"), "evaluated_at": ev.get("at")}


def _incident_brief(i: dict) -> dict:
    keep = ("id", "status", "severity", "title", "cause", "customer_impact", "summary", "load_id", "assignment_id", "revision",
            "created_at", "updated_at", "recommended_plan_id", "explanation", "suppressed_count", "history", "rejected", "also_considered",
            "executed_plan_id", "auto_actions")
    out = {k: i.get(k) for k in keep}
    out["plans"] = [{k: p.get(k) for k in ("plan_id", "title", "kind", "kind_label", "compliance_verdict", "service_tier", "delivery_eta",
                                           "appointment_slack_minutes", "hos_reserve_minutes", "incremental_cost", "cost_breakdown",
                                           "risk_score", "risk_band", "assumptions", "expires_at", "expiry_reason", "required_approvals",
                                           "recheck", "required_stops", "rejected_by")} for p in i.get("plans", [])]
    cp = i.get("current_plan") or {}
    out["current_plan"] = {k: cp.get(k) for k in ("title", "compliance_verdict", "delivery_eta", "appointment_slack_minutes", "hos_reserve_minutes")}
    out["current_failures"] = (i.get("evaluation") or {}).get("hard_failures", [])
    return out


@app.get("/api/state")
def state():
    c = C()
    now = c.clock.now()
    fleet = Fleet.load(c.store)
    incidents = c.store.list_incidents()
    open_first = sorted(incidents, key=lambda i: (i["status"] != "OPEN", i["id"]), reverse=False)
    return {
        "clock": c.clock.status(),
        "health": c.health(),
        "runtime": {"ticks": c.runtime.ticks, "last_error": c.runtime.last_error},
        "policy_versions": policy_versions(),
        "drivers": [_driver_row(d, fleet, now) for d in sorted(fleet.drivers.values(), key=lambda d: d.id)],
        "tractors": [{"id": t.id, "status": t.status, "lat": t.position.lat, "lon": t.position.lon} for t in fleet.tractors.values()],
        "assignments": [_asg_row(a, fleet, c.store) for a in sorted(fleet.assignments.values(), key=lambda a: a.id)],
        "incidents": [_incident_brief(i) for i in open_first[:12]],
        "events": c.store.list_events(25, include_telemetry=False),
        "outbox": c.store.outbox_list(15),
        "explaining": sorted(c.explaining),
    }


@app.get("/api/map")
def map_data():
    net = Fleet.load(C().store).net
    return {"locations": [l.model_dump() for l in net.locations.values()],
            "routes": {rid: [{"loc": w.loc, "lat": net.loc(w.loc).lat, "lon": net.loc(w.loc).lon, "minutes": w.minutes} for w in r.waypoints]
                       for rid, r in net.routes.items()}}


@app.get("/api/fleet")
def fleet_state():
    return C().tools.get_fleet_state()


@app.get("/api/drivers/{driver_id}")
def driver(driver_id: str):
    return _call_tool("get_driver_status", {"driver_id": driver_id})


@app.get("/api/loads/{load_id}")
def load_status(load_id: str):
    return _call_tool("get_load_status", {"load_id": load_id})


@app.get("/api/assignments/{assignment_id}/evaluation")
def assignment_eval(assignment_id: str):
    c = C()
    fleet = Fleet.load(c.store)
    if assignment_id not in fleet.assignments:
        raise HTTPException(404, "unknown assignment")
    return evaluate_assignment(fleet, assignment_id, c.clock.now())


def _call_tool(name: str, args: dict):
    out = C().tools.call(name, args, actor="api")
    if isinstance(out, dict) and out.get("error") == "NOT_FOUND":
        raise HTTPException(404, out["detail"])
    return out


# ---------------------------------------------------------------- events
class EventIn(BaseModel):
    type: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any] = {}
    source: str = "api"
    occurred_at: datetime | None = None
    idempotency_key: str | None = None


@app.post("/api/events")
def post_event(e: EventIn):
    c = C()
    now = c.clock.now()
    ev = make_event(e.type, e.entity_type, e.entity_id, e.payload, e.source, e.occurred_at or now, now, e.idempotency_key)
    return c.engine.process_event(ev)


@app.get("/api/events")
def list_events(limit: int = 50, telemetry: bool = False):
    return C().store.list_events(limit, include_telemetry=telemetry)


@app.get("/api/scenarios")
def scenarios():
    raw = load_yaml(SYNTHETIC_DIR / "scenarios.yaml")
    return [{"name": k, "title": v["title"], "events": len(v["events"])} for k, v in raw.items()]


@app.post("/api/scenarios/{name}/inject")
def inject(name: str):
    c = C()
    raw = load_yaml(SYNTHETIC_DIR / "scenarios.yaml")
    if name not in raw:
        raise HTTPException(404, "unknown scenario")
    now = c.clock.now()
    results = []
    for i, e in enumerate(raw[name]["events"]):
        ev = make_event(e["type"], e["entity_type"], e["entity_id"], e.get("payload", {}), e["source"],
                        now + timedelta(minutes=e.get("offset_minutes", 0)), now, idempotency_key=f"scenario:{name}:{i}")
        results.append(c.engine.process_event(ev))
    return {"scenario": name, "results": results}


# ---------------------------------------------------------------- incidents & approvals
@app.get("/api/incidents")
def incidents(status: str | None = None):
    return [_incident_brief(i) for i in C().store.list_incidents(status)]


@app.get("/api/incidents/{incident_id}")
def incident(incident_id: str):
    i = C().store.get_incident(incident_id)
    if not i:
        raise HTTPException(404, "unknown incident")
    return i


@app.get("/api/incidents/{incident_id}/trace")
def trace(incident_id: str):
    return decision_trace(C().store, incident_id)


class Actor(BaseModel):
    actor: str
    reason: str = ""


class ExecIn(BaseModel):
    approval_token: str
    idempotency_key: str


def _guard(fn, *a):
    try:
        return fn(*a)
    except service.ApprovalError as e:
        raise HTTPException(409, str(e))


@app.post("/api/plans/{plan_id}/approve")
def approve(plan_id: str, body: Actor):
    c = C()
    return _guard(service.approve_plan, c.store, c.clock, plan_id, body.actor)


@app.post("/api/plans/{plan_id}/execute")
def execute(plan_id: str, body: ExecIn):
    c = C()
    return _guard(service.execute_approved_plan, c.store, c.clock, plan_id, body.approval_token, body.idempotency_key)


@app.post("/api/plans/{plan_id}/approve-and-execute")
def approve_and_execute(plan_id: str, body: Actor):
    """Dashboard shortcut: the dispatcher's click is the approval."""
    c = C()
    tok = _guard(service.approve_plan, c.store, c.clock, plan_id, body.actor)
    res = _guard(service.execute_approved_plan, c.store, c.clock, plan_id, tok["approval_token"], f"ui:{plan_id}")
    c.engine.sweep()
    return res


@app.post("/api/plans/{plan_id}/reject")
def reject(plan_id: str, body: Actor):
    c = C()
    return _guard(service.reject_plan, c.store, c.clock, plan_id, body.actor, body.reason)


@app.post("/api/incidents/{incident_id}/override")
def override(incident_id: str, body: Actor):
    c = C()
    return _guard(service.record_override, c.store, c.clock, incident_id, body.actor, body.reason, f"override:{incident_id}:{uuid.uuid4().hex[:8]}")


@app.post("/api/incidents/{incident_id}/draft-customer-message")
def draft_msg(incident_id: str, plan_id: str):
    c = C()
    return service.draft_customer_message(c.store, Fleet.load(c.store), incident_id, plan_id, c.clock.now())


@app.get("/api/outbox")
def outbox(status: str | None = None, limit: int = 50):
    return C().store.outbox_list(limit, status)


# ---------------------------------------------------------------- local models
class ChatIn(BaseModel):
    message: str
    incident_id: str | None = None


@app.post("/api/agent/chat")
def agent_chat(body: ChatIn):
    c = C()
    ctxt = None
    if body.incident_id:
        from ..agent.tools import compact
        ctxt = compact(c.tools.get_incident(body.incident_id), 8000)
    try:
        return run_agent(c.tools, c.llm, body.message, context=ctxt)
    except Exception as e:
        raise HTTPException(503, f"local model unavailable: {e}")


@app.post("/api/incidents/{incident_id}/explain")
def explain(incident_id: str):
    c = C()
    if not c.store.get_incident(incident_id):
        raise HTTPException(404, "unknown incident")
    c.explaining.add(incident_id)
    try:
        out = explain_incident(c.tools, c.llm, incident_id)
    except Exception as e:
        raise HTTPException(503, f"local model unavailable: {e}")
    finally:
        c.explaining.discard(incident_id)
    with c.store.lock:
        inc = c.store.get_incident(incident_id)
        inc["explanation"] = {"text": out["answer"], "model": out["model"], "at": c.clock.now().isoformat(), "steps": out["steps"],
                              "elapsed_s": out["elapsed_s"]}
        c.store.put_incident(inc)
    c.store.audit("explanation", f"incident:{incident_id}", {"model": out["model"], "steps": out["steps"]}, c.clock.now().isoformat())
    return out


class TextIntake(BaseModel):
    text: str
    source: str = "dispatcher-note"
    submit: bool = False


def _maybe_submit(c: Ctx, out: dict, submit: bool, threshold: float = 0.75) -> dict:
    ev = out.get("proposed_event")
    if submit and ev and ev.get("entity_id") and (out.get("confidence") or 0) >= threshold:
        now = c.clock.now()
        out["submitted"] = c.engine.process_event(make_event(ev["type"], ev["entity_type"], ev["entity_id"], ev["payload"], ev["source"], now))
    else:
        out["submitted"] = None
        if submit:
            out["not_submitted_reason"] = "confidence below threshold or unresolved entity; confirm manually"
    return out


@app.post("/api/intake/text")
def intake_text(body: TextIntake):
    c = C()
    try:
        out = text_to_event(c.llm, Fleet.load(c.store), body.text, body.source, c.clock.now())
    except Exception as e:
        raise HTTPException(503, f"local model unavailable: {e}")
    return _maybe_submit(c, out, body.submit)


@app.post("/api/intake/document")
async def intake_document(file: UploadFile = File(...), source: str = Form("document-upload"), submit: bool = Form(False)):
    c = C()
    data = await file.read()
    try:
        out = document_to_event(c.vlm, Fleet.load(c.store), data, f"{source}:{file.filename}", c.clock.now())
    except Exception as e:
        raise HTTPException(503, f"local vision model unavailable: {e}")
    return _maybe_submit(c, out, submit)


# ---------------------------------------------------------------- demo controls
class ClockIn(BaseModel):
    speed: float | None = None
    paused: bool | None = None
    advance_minutes: float | None = None


@app.get("/api/clock")
def clock():
    return C().clock.status()


@app.post("/api/clock")
def set_clock(body: ClockIn):
    c = C()
    if body.advance_minutes:
        c.clock.advance(body.advance_minutes)
    return c.clock.set(speed=body.speed, paused=body.paused)


@app.post("/api/reset")
def reset(speed: float | None = None):
    C().reset(speed)
    return {"ok": True, "clock": C().clock.status()}


@app.get("/api/drivers/{driver_id}/detail")
def driver_detail(driver_id: str):
    c = C()
    now = c.clock.now()
    fleet = Fleet.load(c.store)
    d = fleet.drivers.get(driver_id)
    if not d:
        raise HTTPException(404, "unknown driver")
    a = fleet.assignment_for_driver(driver_id)
    sim = None
    if a:
        pb = Planner(fleet).build(a, now)
        sim = next((l.sim for l in pb.legs if l.driver.id == driver_id), None)
    return {"row": _driver_row(d, fleet, now), "driver": d.model_dump(mode="json"),
            "assignment": _asg_row(a, fleet, c.store) if a else None,
            "risk": score_schedule_risk(d, sim, now, fleet.net),
            "home_terminal": fleet.net.loc(d.home_terminal).name,
            "tractor": fleet.tractors[d.tractor_id].model_dump(mode="json") if d.tractor_id in fleet.tractors else None,
            "outbox": [o for o in c.store.outbox_list(200) if o["recipient"] == driver_id][:10]}


@app.get("/api/assignments/{assignment_id}/detail")
def assignment_detail(assignment_id: str):
    c = C()
    fleet = Fleet.load(c.store)
    a = fleet.assignments.get(assignment_id)
    if not a:
        raise HTTPException(404, "unknown assignment")
    load = fleet.loads[a.load_id]
    ev = evaluate_assignment(fleet, assignment_id, c.clock.now()) if a.driver_id and a.status not in ("COMPLETED", "CANCELLED") else None
    return {"row": _asg_row(a, fleet, c.store), "assignment": a.model_dump(mode="json"), "load": load.model_dump(mode="json"),
            "customer": fleet.customers[load.customer_id].model_dump(mode="json"), "evaluation": ev,
            "incidents": [_incident_brief(i) for i in c.store.list_incidents() if i["assignment_id"] == assignment_id]}


@app.get("/api/config")
def config_view():
    return {"ruleset": load_yaml(CONFIG_DIR / "rulesets" / "us_federal_property_v1.yaml"),
            "company_policy": load_yaml(CONFIG_DIR / "rulesets" / "company_policy_v1.yaml"),
            "cost_model": load_yaml(CONFIG_DIR / "cost_models" / "default_v1.yaml"),
            "risk_policy": load_yaml(CONFIG_DIR / "risk_policy_v1.yaml"),
            "customers": [c.model_dump(mode="json") for c in Fleet.load(C().store).customers.values()],
            "versions": policy_versions()}


@app.get("/api/audit")
def audit(limit: int = 100):
    return C().store.audit_recent(limit)


@app.get("/api/health")
def health():
    c = C()
    return {"api": "ok", **c.health(), "runtime": {"ticks": c.runtime.ticks, "last_error": c.runtime.last_error},
            "clock": c.clock.status(), "policy_versions": policy_versions()}
