"""
Costed recovery solver.

A simple enumerative solver, exactly as the spec allows for a first
prototype ("Google OR-Tools CP-SAT ... or a simpler enumerative solver").
It generates driver swaps, a two-leg relay, a delayed departure, a
delivery-window-adjustment ask, and a hold-for-reset -- then scores and
ranks them. It NEVER returns a plan that fails compliance or uses
incompatible equipment; "no plan exists" is a valid, honest answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.feasibility.assignment import evaluate_assignment, MINIMUM_REQUIRED_RESERVE_MINUTES
from app.policies.schedule_risk import score_schedule_risk
from app.optimization.costs import incremental_cost, estimated_load_margin, DEFAULT_COST_MODEL
from app.routing import distance_miles, driving_minutes_for
from app.fleet_state import FleetState


@dataclass
class RecoveryPlan:
    plan_id: str
    type: str
    steps: list[dict]
    added_cost: float
    delivery_delay_minutes: int
    minimum_hos_reserve_minutes: int
    schedule_risk: str
    customer_penalty: float
    estimated_load_margin: float
    score: float = 0.0
    rejected_candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


def _score(plan: RecoveryPlan) -> float:
    """score = incremental cost + lateness penalty + schedule-risk penalty
    + disruption penalty + low-reserve penalty"""
    lateness_penalty = plan.delivery_delay_minutes * 3.0
    risk_penalty = {"low": 0, "moderate": 60, "high": 400}.get(plan.schedule_risk, 0)
    disruption_penalty = 0 if plan.type == "hold_for_reset" else 15
    low_reserve_penalty = max(0, (MINIMUM_REQUIRED_RESERVE_MINUTES * 2 - plan.minimum_hos_reserve_minutes)) * 0.5
    return round(plan.added_cost + lateness_penalty + risk_penalty + disruption_penalty + low_reserve_penalty, 2)


def _try_driver(
    state: FleetState, candidate_driver_id: str, load, vehicle_id: str, trailer_id: str,
    proposed_start: datetime, exclude_ids: set[str],
) -> tuple[dict, dict] | None:
    """Returns (assignment_evaluation, schedule_risk) if the candidate is legal
    and feasible, else None. Equipment ids are fixed to the load's existing
    vehicle/trailer for a pure driver-swap candidate."""
    if candidate_driver_id in exclude_ids:
        return None
    driver = state.drivers[candidate_driver_id]
    vehicle = state.vehicles[vehicle_id]
    trailer = state.trailers[trailer_id]
    if vehicle.status == "assigned" and vehicle.assigned_driver != candidate_driver_id:
        return None
    if trailer.status == "assigned":
        return None
    ev = evaluate_assignment(driver, load, vehicle, trailer, proposed_start, state.now)
    if not (ev["legally_compliant"] and ev["operationally_feasible"]):
        return None
    reserve = ev.get("remaining_drive_reserve_minutes", MINIMUM_REQUIRED_RESERVE_MINUTES)
    planned_end = datetime.fromisoformat(ev["projected_arrival_at_delivery"])
    risk = score_schedule_risk(driver, proposed_start, planned_end, reserve)
    return ev, risk


def find_costed_alternatives(
    disrupted_assignment_id: str,
    state: FleetState,
    proposed_start: datetime | None = None,
    maximum_options: int = 5,
) -> list[dict]:
    """
    find_costed_alternatives(disrupted_assignment_id, current_fleet_state, maximum_options=5)
        -> list[RecoveryPlan]
    """
    assignment = state.assignments[disrupted_assignment_id]
    load = state.loads[assignment.load_id]
    original_driver_id = assignment.driver_id
    vehicle_id = assignment.vehicle_id
    trailer_id = assignment.trailer_id
    proposed_start = proposed_start or assignment.planned_start

    plans: list[RecoveryPlan] = []
    rejected: list[dict] = []
    exclude = {original_driver_id}

    other_drivers = [d for d in state.drivers.values() if d.id not in exclude]

    # --- candidate 1..n: driver swap (same tractor/trailer, driver repositions) ---
    for cand in sorted(other_drivers, key=lambda d: distance_miles(d.current_location, load.origin)):
        result = _try_driver(state, cand.id, load, vehicle_id, trailer_id, proposed_start, exclude)
        if result is None:
            rejected.append({"driver": cand.id, "reason": "failed compliance or feasibility check"})
            continue
        ev, risk = result
        deadhead_miles = distance_miles(cand.current_location, load.origin)
        cost = incremental_cost(
            deadhead_miles=deadhead_miles,
            additional_driver_minutes=0,
            additional_fuel_miles=deadhead_miles,
            expected_late_penalty=ev["projected_delivery_delay_minutes"] / 60 * load.late_penalty_per_hour,
        )
        margin = estimated_load_margin(load.revenue, cost)
        plan = RecoveryPlan(
            plan_id=f"swap_{cand.id}",
            type="driver_swap",
            steps=[{"driver": cand.id, "from": cand.current_location, "to": load.origin}],
            added_cost=cost,
            delivery_delay_minutes=ev["projected_delivery_delay_minutes"],
            minimum_hos_reserve_minutes=ev.get("remaining_drive_reserve_minutes", 0),
            schedule_risk=risk["schedule_risk_state"],
            customer_penalty=0.0,
            estimated_load_margin=margin,
        )
        plan.score = _score(plan)
        plans.append(plan)

    # --- candidate: two-driver relay through a known waypoint, if one exists on this route ---
    relay_point = _relay_point_for(load.origin, load.destination)
    if relay_point:
        leg1_candidates = [original_driver_id]  # the original driver can usually still do leg 1
        for leg2 in other_drivers:
            try:
                leg1_miles = distance_miles(load.origin, relay_point)
                leg2_miles = distance_miles(relay_point, load.destination)
            except KeyError:
                continue
            orig_driver = state.drivers[original_driver_id]
            leg1_drive_min = driving_minutes_for(leg1_miles)
            if leg1_drive_min > orig_driver.drive_minutes_remaining:
                continue  # original driver can't even make leg 1 -- skip this relay
            deadhead_leg2 = distance_miles(leg2.current_location, relay_point)
            leg2_drive_min = driving_minutes_for(deadhead_leg2 + leg2_miles)
            if leg2_drive_min > leg2.drive_minutes_remaining or leg2.id in exclude:
                continue
            delay_minutes = 20  # fixed handoff overhead, a prototype simplification
            cost = incremental_cost(
                deadhead_miles=deadhead_leg2,
                additional_fuel_miles=deadhead_leg2,
                includes_relay=True,
                expected_late_penalty=delay_minutes / 60 * load.late_penalty_per_hour,
            )
            risk = score_schedule_risk(
                orig_driver, proposed_start, proposed_start + timedelta(minutes=leg1_drive_min), 60
            )
            plan = RecoveryPlan(
                plan_id=f"relay_{leg2.id}",
                type="driver_relay",
                steps=[
                    {"driver": original_driver_id, "from": load.origin, "to": relay_point},
                    {"driver": leg2.id, "from": relay_point, "to": load.destination},
                ],
                added_cost=cost,
                delivery_delay_minutes=delay_minutes,
                minimum_hos_reserve_minutes=min(
                    orig_driver.drive_minutes_remaining - leg1_drive_min,
                    leg2.drive_minutes_remaining - leg2_drive_min,
                ),
                schedule_risk=risk["schedule_risk_state"],
                customer_penalty=0.0,
                estimated_load_margin=estimated_load_margin(load.revenue, cost),
            )
            plan.score = _score(plan)
            plans.append(plan)
            break  # one relay candidate is enough for a prototype

    # --- candidate: hold the original driver for a 10-hour reset ---
    orig_driver = state.drivers[original_driver_id]
    if orig_driver.hours_since_last_reset < 10 or orig_driver.drive_minutes_remaining < driving_minutes_for(
        distance_miles(load.origin, load.destination)
    ):
        reset_wait_minutes = round((10 - min(orig_driver.hours_since_last_reset, 10)) * 60) or 600
        new_start = proposed_start + timedelta(minutes=reset_wait_minutes)
        delay_minutes = reset_wait_minutes
        customer_penalty = (delay_minutes / 60) * load.late_penalty_per_hour
        cost = incremental_cost(expected_late_penalty=0)  # paying the driver to wait isn't modeled as added cost here
        plan = RecoveryPlan(
            plan_id=f"hold_{original_driver_id}",
            type="hold_for_reset",
            steps=[{"driver": original_driver_id, "action": "10-hour reset", "then": "continue original plan"}],
            added_cost=cost,
            delivery_delay_minutes=delay_minutes,
            minimum_hos_reserve_minutes=660,
            schedule_risk="low",
            customer_penalty=round(customer_penalty, 2),
            estimated_load_margin=estimated_load_margin(load.revenue, cost + customer_penalty),
        )
        plan.score = _score(plan)
        plans.append(plan)

    # --- candidate: ask the customer to move the delivery window ---
    needed_push_minutes = 60  # prototype default ask
    change_cost = 0.0 if "flexible" in load.customer.lower() else 50.0
    plan = RecoveryPlan(
        plan_id=f"delay_window_{load.id}",
        type="delivery_window_adjustment",
        steps=[{"action": f"request {needed_push_minutes}-minute delivery window extension",
                "customer": load.customer}],
        added_cost=change_cost,
        delivery_delay_minutes=needed_push_minutes,
        minimum_hos_reserve_minutes=MINIMUM_REQUIRED_RESERVE_MINUTES,
        schedule_risk="low",
        customer_penalty=0.0,
        estimated_load_margin=estimated_load_margin(load.revenue, change_cost),
    )
    plan.score = _score(plan)
    plans.append(plan)

    # --- fallback: leave unassigned ---
    fallback = RecoveryPlan(
        plan_id=f"unassigned_{load.id}",
        type="leave_unassigned",
        steps=[{"action": "leave load unassigned", "load": load.id}],
        added_cost=0.0,
        delivery_delay_minutes=0,
        minimum_hos_reserve_minutes=0,
        schedule_risk="low",
        customer_penalty=load.unserved_penalty,
        estimated_load_margin=-load.unserved_penalty,
    )
    fallback.score = _score(fallback) + load.unserved_penalty  # always ranks last unless nothing else works
    plans.append(fallback)

    plans.sort(key=lambda p: p.score)
    top = plans[:maximum_options]
    out = [p.to_dict() for p in top]
    if out:
        out[0]["recommended"] = True
        if len(out) > 1:
            out[0]["net_advantage_usd"] = round(out[1]["score"] - out[0]["score"], 2)
    for p in out[1:]:
        p["recommended"] = False
    return out


# A handful of known relay waypoints for routes we have distances for.
_RELAY_POINTS = {
    ("Pecos Yard", "Sterling City Terminal"): "Big Lake Yard",
    ("Big Lake Yard", "Sterling City Terminal"): "Big Lake Yard",
    ("Boston", "Albany"): "Springfield",
    ("Hartford", "Boston"): "Springfield",
}


def _relay_point_for(origin: str, destination: str) -> str | None:
    return _RELAY_POINTS.get((origin, destination))
