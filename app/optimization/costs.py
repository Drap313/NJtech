"""Cost model. Absolute plan costs are computed per component; recovery plans
report the component-wise delta against continuing the current plan."""
from __future__ import annotations

from datetime import datetime

from ..clock import minutes_between, utc
from ..compliance.hos import ON
from ..config import CostModel, get_cost_model
from ..forecasting.planner import PlanBuild
from ..models import CustomerTerms


def late_penalty(terms: CustomerTerms, late_minutes: float) -> float:
    if late_minutes <= 0:
        return 0.0
    raw = terms.late_flat + terms.late_per_hour * late_minutes / 60.0
    return round(min(terms.late_cap, raw) if terms.late_cap else raw, 2)


def plan_cost_components(pb: PlanBuild, terms: CustomerTerms, delivery_slack: float | None, now: datetime,
                         risk_score: int = 0, cm: CostModel | None = None) -> dict:
    cm = cm or get_cost_model()
    free = cm.op("free_wait_minutes")
    labor = idle = 0.0
    empty = pb.extra_empty_miles
    for leg in pb.legs:
        if not leg.sim:
            continue
        labor += leg.sim.on_duty_minutes_after(max(utc(leg.labor_from), utc(leg.sim.start)))
        empty += leg.sim.miles(empty=True)
        for e in leg.sim.entries:
            if e.status == ON and e.stop == "wait" and e.end > now and leg.carries_cargo:
                idle += max(0.0, minutes_between(max(e.start, now), e.end) - free)
    late = max(0.0, -delivery_slack) if delivery_slack is not None else 0.0
    return {
        "labor_minutes": round(labor),
        "empty_miles": round(empty, 1),
        "shuttle_miles": round(pb.shuttle_miles, 1),
        "idle_minutes": round(idle),
        "handling": list(pb.handling),
        "premiums": [list(p) for p in pb.premiums],
        "late_minutes": round(late),
        "late_penalty": late_penalty(terms, late) if not pb.appointment_change else 0.0,
        "risk_points": risk_score,
    }


def incremental_cost(plan: dict, base: dict, cm: CostModel | None = None) -> tuple[float, list[dict]]:
    cm = cm or get_cost_model()
    lines: list[dict] = []

    def add(component: str, amount: float, coef: str | None, detail: str, basis: str | None = None):
        if abs(amount) < 0.005:
            return
        lines.append({"component": component, "amount": round(amount, 2), "basis": basis or (cm.basis(coef) if coef else "estimate"),
                      "detail": detail})

    d_empty = plan["empty_miles"] - base["empty_miles"]
    add("Reposition / empty miles", d_empty * cm.c("cost_per_mile"), "cost_per_mile", f"{d_empty:+.0f} mi x ${cm.c('cost_per_mile'):.2f}")
    d_sh = plan["shuttle_miles"] - base["shuttle_miles"]
    add("Driver shuttle", d_sh * cm.c("shuttle_cost_per_mile"), "shuttle_cost_per_mile", f"{d_sh:+.0f} mi x ${cm.c('shuttle_cost_per_mile'):.2f}")
    d_lab = plan["labor_minutes"] - base["labor_minutes"]
    add("Driver time", d_lab * cm.c("labor_cost_per_minute"), "labor_cost_per_minute",
        f"{d_lab:+d} on-duty min x ${cm.c('labor_cost_per_minute'):.2f}")
    d_idle = plan["idle_minutes"] - base["idle_minutes"]
    add("Detention / idle", d_idle * cm.c("idle_cost_per_minute"), "idle_cost_per_minute", f"{d_idle:+d} min beyond free time")
    for h in plan["handling"]:
        add(h.replace("_cost", "").replace("_", " ").capitalize(), cm.c(h), h, "per occurrence")
    for coef, who in plan["premiums"]:
        add(coef.replace("_", " ").capitalize(), cm.c(coef), coef, who)
    d_pen = plan["late_penalty"] - base["late_penalty"]
    if d_pen:
        add("Late-delivery exposure", d_pen, None, f"customer terms; projected {plan['late_minutes']} min late", basis="contract")
    risk = (plan["risk_points"] - base["risk_points"]) * cm.c("risk_penalty_per_point")
    add("Risk penalty", risk, "risk_penalty_per_point", f"{plan['risk_points'] - base['risk_points']:+d} risk points")
    return round(sum(l["amount"] for l in lines), 2), lines
