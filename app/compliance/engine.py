"""Compliance verdicts. Pure code: the language model may call this and explain
its output but can never compute or override a verdict."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from ..clock import minutes_between, utc
from ..config import Rules, get_rules
from ..forecasting.planner import PlanBuild
from ..models import Load
from ..state import Fleet
from .hos import DRIVING, ON, SimResult
from .qualifications import check_driver, check_equipment

VERDICT_ORDER = ["PASS", "MANUAL_REVIEW", "UNKNOWN", "FAIL"]


def _fmt(dt: datetime, tz: str) -> str:
    return utc(dt).astimezone(ZoneInfo(tz)).strftime("%a %H:%M %Z")


def _crosses_midnight(sim: SimResult, tz: str) -> bool:
    end = sim.last_drive_end
    if end is None:
        return False
    z = ZoneInfo(tz)
    return sim.start.astimezone(z).date() != end.astimezone(z).date()


def check_plan(fleet: Fleet, pb: PlanBuild, load: Load, now: datetime, rules: Rules | None = None) -> dict:
    R = rules or get_rules()
    now = utc(now)
    net = fleet.net
    hard, warns, manual, unknowns, trace = [], [], [], [], []
    per_driver: dict[str, dict] = {}
    freshness: dict[str, dict] = {}

    if pb.errors:
        return _result("FAIL", [{"code": "PLAN_INFEASIBLE", "detail": e} for e in pb.errors], [], [], [], {}, {}, [], R, now)

    plan_end = pb.marks.get("delivered") or max((l.sim.end.t for l in pb.legs if l.sim), default=now)
    for leg in pb.legs:
        d, sim = leg.driver, leg.sim
        tz = net.loc(d.home_terminal).tz
        age = minutes_between(utc(d.eld.observed_at), now)
        fresh = age <= R.max_eld_age
        freshness[f"{d.id}.eld"] = {"observed_at": d.eld.observed_at.isoformat(), "source": d.eld.source,
                                    "age_minutes": round(age), "limit_minutes": R.max_eld_age, "fresh": fresh}
        trace.append({"rule": "DATA_FRESHNESS", "citation": f"company policy {R.company_id}", "subject": d.id,
                      "status": "PASS" if fresh else "UNKNOWN", "detail": f"ELD snapshot age {age:.0f} min (limit {R.max_eld_age})"})
        if not fresh:
            unknowns.append({"code": "STALE_ELD", "driver_id": d.id, "age_minutes": round(age),
                             "detail": f"{d.id} ELD last reported {age:.0f} min ago ({d.eld.source})"})
        for exc in d.claimed_exceptions:
            if exc in R.unsupported_exceptions:
                manual.append({"code": "UNSUPPORTED_EXCEPTION", "driver_id": d.id, "detail": f"{exc} is not modeled by {R.ruleset_id}"})

        # Hours of service
        cyc_ok_for_review = _crosses_midnight(sim, tz)
        for code, v in sim.violations.items():
            item = {"code": code, "driver_id": d.id, "excess_minutes": v.excess_minutes, "first_at": v.first_at.isoformat(),
                    "citation": R.cite(code), "detail": _violation_detail(code, v, d.id, tz)}
            if code == "CYCLE_LIMIT" and cyc_ok_for_review:
                item["code"] = "CYCLE_ROLLOFF_NOT_MODELED"
                item["detail"] += "; plan crosses midnight and daily cycle roll-off is not modeled, so a human must confirm"
                manual.append(item)
            else:
                hard.append(item)
        res = sim.reserve
        drives = res is not None
        for code, limit_key in (("DRIVE_LIMIT", "drive_left"), ("SHIFT_LIMIT", "window_left"), ("CYCLE_LIMIT", "cycle_left")):
            v = sim.violations.get(code)
            if v:
                status, detail = "FAIL", f"exceeded by {v.excess_minutes} min"
            elif drives:
                status, detail = "PASS", f"tightest remaining {res[limit_key]} min"
            else:
                status, detail = "PASS", "no driving in plan"
            trace.append({"rule": code, "citation": R.cite(code), "subject": d.id, "status": status, "detail": detail})
        for b in sim.breaks:
            trace.append({"rule": "BREAK_REQUIRED", "citation": R.cite("BREAK_REQUIRED"), "subject": d.id, "status": "PASS",
                          "detail": f"30-min break inserted at {_fmt(b.start, tz)} after 8 h cumulative driving"})
        for r in sim.resets:
            trace.append({"rule": "OFF_DUTY_RESET", "citation": R.cite("OFF_DUTY_RESET"), "subject": d.id, "status": "PASS",
                          "detail": f"{r.minutes} min off duty from {_fmt(r.start, tz)} resets 11/14-hour clocks"})
        if drives:
            reserve = res["reserve"]
            if reserve >= 0 and reserve < R.min_reserve:
                hard.append({"code": "COMPANY_MIN_RESERVE", "driver_id": d.id, "remaining_minutes": reserve,
                             "citation": f"company policy {R.company_id}",
                             "detail": f"{d.id} projected legal reserve {reserve} min is below company minimum {R.min_reserve}"})
            elif 0 <= reserve < R.low_reserve_warn:
                warns.append({"code": "LOW_RESERVE", "driver_id": d.id, "remaining_minutes": reserve,
                              "detail": f"{d.id} projected reserve {reserve} min ({res['binding'].replace('_left', '')} clock)"})
            per_driver[d.id] = {"role": leg.role, "drive_left_minutes": res["drive_left"], "shift_left_minutes": res["window_left"],
                                "cycle_left_minutes": res["cycle_left"], "reserve_minutes": reserve, "binding": res["binding"],
                                "window_end": res["window_end"].isoformat()}

        # Qualifications only matter for legs that move the customer's freight.
        if leg.carries_cargo:
            tractor = fleet.tractors.get(leg.tractor_id) if leg.tractor_id else None
            start = next((e.start for e in sim.entries if e.status in (ON, DRIVING) and e.stop != "status"),
                         leg.start or utc(d.eld.observed_at))
            f, t = check_driver(d, load, tractor, net, R, start, plan_end)
            hard += f
            trace += t

    tractors = {l.tractor_id for l in pb.legs if l.carries_cargo and l.tractor_id}
    trailer = fleet.trailers.get(pb.trailer_id) if pb.trailer_id else None
    for i, tid in enumerate(sorted(tractors) or [None]):
        f, t = check_equipment(load, fleet.tractors.get(tid) if tid else None, trailer if i == 0 else None, R, now, plan_end)
        hard += f
        trace += t

    if hard:
        verdict = "FAIL"
    elif unknowns:
        verdict = "UNKNOWN"
    elif manual:
        verdict = "MANUAL_REVIEW"
    else:
        verdict = "PASS"
    projected = {}
    if per_driver:
        tight = min(per_driver.values(), key=lambda x: x["reserve_minutes"])
        projected = {"drive_left_minutes": tight["drive_left_minutes"], "shift_left_minutes": tight["shift_left_minutes"],
                     "cycle_left_minutes": tight["cycle_left_minutes"]}
    return _result(verdict, hard, warns, manual, unknowns, projected, per_driver, trace, R, now, freshness)


def _violation_detail(code: str, v, driver_id: str, tz: str) -> str:
    what = {
        "SHIFT_LIMIT": "driving past the 14-hour window",
        "DRIVE_LIMIT": "driving beyond 11 hours",
        "CYCLE_LIMIT": "driving beyond the 60/70-hour cycle",
        "BREAK_REQUIRED": "driving beyond 8 hours without a 30-minute break",
        "COMPANY_DRIVE_LIMIT": "driving beyond the company driving limit",
        "COMPANY_WINDOW_LIMIT": "driving beyond the company duty window",
    }.get(code, code)
    return f"{driver_id} projected {v.excess_minutes} min of {what}, starting {_fmt(v.first_at, tz)}"


def _result(verdict, hard, warns, manual, unknowns, projected, per_driver, trace, R: Rules, now, freshness=None) -> dict:
    return {
        "verdict": verdict,
        "hard_failures": hard,
        "warnings": warns,
        "manual_review": manual,
        "unknowns": unknowns,
        "projected_clocks": projected,
        "per_driver": per_driver,
        "ruleset": f"{R.ruleset_id}@{R.ruleset_version}",
        "company_policy": f"{R.company_id}@{R.company_version}",
        "rule_trace": trace,
        "input_freshness": freshness or {},
        "evaluated_at": now.isoformat(),
    }
