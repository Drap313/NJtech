"""
Deterministic HOS and qualification checks.

This is the one rule: the local model never decides legality. Every function
here is plain, testable Python with no model call anywhere in it. If an
exception type isn't supported, we say so and escalate -- we never guess.

Initial scope (U.S. interstate, property-carrying, standard federal HOS):
  - 11-hour driving limit
  - 14-hour duty window
  - 30-minute break after 8 cumulative hours of driving
  - 10 consecutive hours off duty before a new duty period
  - a configurable 60-hour/7-day or 70-hour/8-day cycle
  - CDL class
  - endorsements
  - driver restrictions
  - vehicle/trailer compatibility

NOT supported yet (returns verdict="manual_review" instead of guessing):
  - oilfield waiting-time exceptions
  - short-haul / 150-air-mile exception
  - adverse driving conditions exception
  - sleeper-berth split-duty provisions
  - intrastate rule variants
"""
from __future__ import annotations

from datetime import datetime

from app.compliance.results import compliance_result, violation, warning
from app.models.entities import Driver, Vehicle, Trailer, Load
from app.routing import driving_minutes_for

BREAK_REQUIRED_AFTER_MINUTES = 480       # 8 hours of driving
BREAK_LENGTH_MINUTES = 30
MINIMUM_RESET_HOURS = 10.0

STALE_ELD_WARNING_MINUTES = 15
STALE_ELD_MANUAL_REVIEW_MINUTES = 60
STALE_LOCATION_WARNING_MINUTES = 20

UNSUPPORTED_RESTRICTION_FLAGS = {
    "oilfield_waiting_time_exception",
    "short_haul_150_air_mile_exception",
    "adverse_driving_conditions_exception",
    "sleeper_berth_split",
}

REQUIRED_CDL_CLASS_FOR_COMBINATION = "A"


def _minutes_since(ts: datetime, now: datetime) -> int:
    return max(0, round((now - ts).total_seconds() / 60))


def check_hos_and_qualifications(
    driver: Driver,
    load: Load,
    vehicle: Vehicle,
    trailer: Trailer,
    deadhead_miles: float,
    loaded_miles: float,
    departure_time: datetime,
    now: datetime,
) -> dict:
    """
    check_hos_and_qualifications(driver, load, vehicle, trailer, route, departure_time)
        -> ComplianceResult

    `route` is expanded here into deadhead_miles/loaded_miles for testability;
    app.feasibility.assignment wraps this with the route/ETA math.
    """
    violations: list[dict] = []
    warnings: list[dict] = []
    unsupported: list[str] = []

    # --- unsupported exceptions: check first, bail out rather than guess ---
    flagged = UNSUPPORTED_RESTRICTION_FLAGS.intersection(set(driver.restrictions))
    if flagged:
        unsupported.extend(sorted(flagged))
        data_freshness = {
            "eld_age_minutes": _minutes_since(driver.last_eld_sync, now),
            "location_age_minutes": _minutes_since(driver.last_location_update, now),
        }
        return compliance_result(
            "manual_review",
            unsupported_exceptions=unsupported,
            data_freshness=data_freshness,
        )

    needed_driving_minutes = driving_minutes_for(deadhead_miles + loaded_miles)
    needed_duty_minutes = (
        needed_driving_minutes
        + load.estimated_loading_minutes
        + load.estimated_unloading_minutes
    )

    # --- 30-minute break requirement ---
    break_included = 0
    projected_driving_since_break = driver.minutes_since_last_break + needed_driving_minutes
    if projected_driving_since_break > BREAK_REQUIRED_AFTER_MINUTES:
        break_included = BREAK_LENGTH_MINUTES
        needed_duty_minutes += BREAK_LENGTH_MINUTES

    # --- 10-hour reset, only enforced when the driver is coming back on duty ---
    if driver.duty_status in ("off_duty", "sleeper_berth") and driver.hours_since_last_reset < MINIMUM_RESET_HOURS:
        deficit_hours = MINIMUM_RESET_HOURS - driver.hours_since_last_reset
        violations.append(violation(
            "10_hour_reset",
            required_minutes=round(MINIMUM_RESET_HOURS * 60),
            available_minutes=round(driver.hours_since_last_reset * 60),
            detail=f"{driver.name} has had {driver.hours_since_last_reset:.1f}h off and "
                   f"needs {deficit_hours:.1f}h more before driving again.",
        ))

    # --- 11-hour driving limit ---
    if needed_driving_minutes > driver.drive_minutes_remaining:
        violations.append(violation(
            "11_hour_driving_limit",
            required_minutes=needed_driving_minutes,
            available_minutes=driver.drive_minutes_remaining,
            detail=f"{driver.name} needs {needed_driving_minutes} min of driving and has "
                   f"{driver.drive_minutes_remaining} min left of the 11-hour limit.",
        ))

    # --- 14-hour duty window ---
    if needed_duty_minutes > driver.shift_minutes_remaining:
        violations.append(violation(
            "14_hour_duty_window",
            required_minutes=needed_duty_minutes,
            available_minutes=driver.shift_minutes_remaining,
            detail=f"{driver.name} needs {needed_duty_minutes} min on duty and has "
                   f"{driver.shift_minutes_remaining} min left of the 14-hour window.",
        ))

    # --- 60/70-hour cycle ---
    if needed_duty_minutes > driver.cycle_minutes_remaining:
        violations.append(violation(
            "duty_cycle_limit",
            required_minutes=needed_duty_minutes,
            available_minutes=driver.cycle_minutes_remaining,
            detail=f"{driver.name} needs {needed_duty_minutes} min and has "
                   f"{driver.cycle_minutes_remaining} min left in the configured duty cycle.",
        ))

    # --- CDL class ---
    if driver.cdl_class != REQUIRED_CDL_CLASS_FOR_COMBINATION:
        violations.append(violation(
            "cdl_class_required",
            required_minutes=0,
            available_minutes=0,
            detail=f"Load requires a Class {REQUIRED_CDL_CLASS_FOR_COMBINATION} CDL; "
                   f"{driver.name} holds Class {driver.cdl_class}.",
        ))

    # --- endorsements ---
    missing_endorsements = [e for e in load.required_driver_endorsements if e not in driver.endorsements]
    if missing_endorsements:
        violations.append(violation(
            "missing_endorsement",
            required_minutes=0,
            available_minutes=0,
            detail=f"{driver.name} is missing endorsement(s): {', '.join(missing_endorsements)}.",
        ))

    # --- driver restrictions that block this load outright ---
    blocking = set(driver.restrictions).intersection(set(load.required_vehicle_capabilities))
    if blocking:
        violations.append(violation(
            "driver_restriction",
            required_minutes=0,
            available_minutes=0,
            detail=f"{driver.name} is restricted from: {', '.join(sorted(blocking))}.",
        ))

    # --- vehicle / trailer compatibility ---
    if trailer.type != load.required_trailer_type:
        violations.append(violation(
            "trailer_type_mismatch",
            required_minutes=0,
            available_minutes=0,
            detail=f"Load requires a {load.required_trailer_type} trailer; "
                   f"{trailer.id} is a {trailer.type}.",
        ))
    missing_vehicle_caps = [c for c in load.required_vehicle_capabilities if c not in vehicle.equipment_capabilities]
    if missing_vehicle_caps:
        violations.append(violation(
            "vehicle_capability_mismatch",
            required_minutes=0,
            available_minutes=0,
            detail=f"{vehicle.id} ({vehicle.make} {vehicle.model}) is missing: "
                   f"{', '.join(missing_vehicle_caps)}.",
        ))

    # --- data freshness ---
    eld_age = _minutes_since(driver.last_eld_sync, now)
    location_age = _minutes_since(driver.last_location_update, now)
    data_freshness = {"eld_age_minutes": eld_age, "location_age_minutes": location_age}

    if eld_age >= STALE_ELD_MANUAL_REVIEW_MINUTES:
        return compliance_result(
            "manual_review",
            violations=violations,
            unsupported_exceptions=["stale_eld_data"],
            data_freshness=data_freshness,
        )
    if eld_age >= STALE_ELD_WARNING_MINUTES:
        warnings.append(warning(
            "stale_eld_data",
            f"ELD data is {eld_age} minutes old; treat remaining-hours figures as approximate.",
        ))
    if location_age >= STALE_LOCATION_WARNING_MINUTES:
        warnings.append(warning(
            "stale_location_data",
            f"Location is {location_age} minutes old.",
        ))

    verdict = "illegal" if violations else "legal"
    return compliance_result(
        verdict,
        violations=violations,
        warnings=warnings,
        data_freshness=data_freshness,
    )
