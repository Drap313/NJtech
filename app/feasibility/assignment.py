"""
Operational feasibility: legal does not mean achievable.

evaluate_assignment() wraps check_hos_and_qualifications() with routing math:
can the driver/vehicle/trailer actually reach the pickup, does the plan clear
the delivery window with a safe reserve, and is the data fresh enough to trust.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.compliance.hos import check_hos_and_qualifications
from app.models.entities import Driver, Vehicle, Trailer, Load
from app.routing import distance_miles, driving_minutes_for

TRAFFIC_BUFFER_PCT = 0.10          # feasibility adds this on top of the raw drive-time estimate
MINIMUM_REQUIRED_RESERVE_MINUTES = 45   # drive-time reserve a plan must leave after delivery


def evaluate_assignment(
    driver: Driver,
    load: Load,
    vehicle: Vehicle,
    trailer: Trailer,
    proposed_start: datetime,
    now: datetime,
) -> dict:
    """
    evaluate_assignment(driver_id, load_id, vehicle_id, trailer_id, proposed_start)
        -> AssignmentEvaluation

    Takes resolved objects rather than ids so it's trivially unit-testable;
    app/api/assignments.py does the id -> object lookup against fleet state.
    """
    deadhead_miles = distance_miles(driver.current_location, load.origin)
    loaded_miles = distance_miles(load.origin, load.destination)

    compliance = check_hos_and_qualifications(
        driver=driver, load=load, vehicle=vehicle, trailer=trailer,
        deadhead_miles=deadhead_miles, loaded_miles=loaded_miles,
        departure_time=proposed_start, now=now,
    )

    legally_compliant = compliance["verdict"] == "legal"

    if compliance["verdict"] == "manual_review":
        return {
            "legally_compliant": None,
            "operationally_feasible": None,
            "reason": "Cannot determine automatically: "
                      + ", ".join(compliance["unsupported_exceptions"])
                      + ". Escalating for manual review.",
            "minimum_required_reserve": MINIMUM_REQUIRED_RESERVE_MINUTES,
            "projected_delivery_delay_minutes": None,
            "compliance": compliance,
        }

    if not legally_compliant:
        first = compliance["violations"][0]
        return {
            "legally_compliant": False,
            "operationally_feasible": False,
            "reason": first["detail"],
            "minimum_required_reserve": MINIMUM_REQUIRED_RESERVE_MINUTES,
            "projected_delivery_delay_minutes": 0,
            "compliance": compliance,
        }

    # Legal. Now check whether the plan actually works operationally.
    deadhead_drive_min = driving_minutes_for(deadhead_miles)
    loaded_drive_min = driving_minutes_for(loaded_miles)
    buffered_drive_min = round((deadhead_drive_min + loaded_drive_min) * (1 + TRAFFIC_BUFFER_PCT))

    arrival_at_pickup = proposed_start + timedelta(minutes=deadhead_drive_min)
    departure_from_pickup = arrival_at_pickup + timedelta(minutes=load.estimated_loading_minutes)
    arrival_at_delivery = departure_from_pickup + timedelta(
        minutes=round(loaded_drive_min * (1 + TRAFFIC_BUFFER_PCT))
    )

    # pickup window
    if arrival_at_pickup > load.pickup_window_end:
        return {
            "legally_compliant": True,
            "operationally_feasible": False,
            "reason": f"Driver would arrive at pickup at {arrival_at_pickup:%H:%M}, "
                      f"after the window closes at {load.pickup_window_end:%H:%M}.",
            "minimum_required_reserve": MINIMUM_REQUIRED_RESERVE_MINUTES,
            "projected_delivery_delay_minutes": 0,
            "compliance": compliance,
        }

    # delivery window
    delay_minutes = max(0, round((arrival_at_delivery - load.delivery_window_end).total_seconds() / 60))
    if delay_minutes > 0:
        return {
            "legally_compliant": True,
            "operationally_feasible": False,
            "reason": f"Projected delivery at {arrival_at_delivery:%H:%M} misses the window "
                      f"by {delay_minutes} minutes.",
            "minimum_required_reserve": MINIMUM_REQUIRED_RESERVE_MINUTES,
            "projected_delivery_delay_minutes": delay_minutes,
            "compliance": compliance,
        }

    # HOS reserve remaining after the plan
    remaining_drive_reserve = driver.drive_minutes_remaining - buffered_drive_min
    if remaining_drive_reserve < MINIMUM_REQUIRED_RESERVE_MINUTES:
        return {
            "legally_compliant": True,
            "operationally_feasible": False,
            "reason": f"Delivery would leave only {remaining_drive_reserve} minutes of driving "
                      f"reserve, below the {MINIMUM_REQUIRED_RESERVE_MINUTES}-minute minimum.",
            "minimum_required_reserve": MINIMUM_REQUIRED_RESERVE_MINUTES,
            "projected_delivery_delay_minutes": 0,
            "compliance": compliance,
        }

    return {
        "legally_compliant": True,
        "operationally_feasible": True,
        "reason": None,
        "minimum_required_reserve": MINIMUM_REQUIRED_RESERVE_MINUTES,
        "projected_delivery_delay_minutes": 0,
        "projected_arrival_at_pickup": arrival_at_pickup.isoformat(),
        "projected_arrival_at_delivery": arrival_at_delivery.isoformat(),
        "remaining_drive_reserve_minutes": remaining_drive_reserve,
        "compliance": compliance,
    }
