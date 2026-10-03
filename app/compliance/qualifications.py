"""Driver qualification and equipment compatibility checks."""
from __future__ import annotations

from datetime import datetime

from ..clock import utc
from ..config import Rules
from ..models import Driver, Load, Tractor, Trailer
from ..routing import Network

# Endorsements that satisfy each requirement (X = combined tank + hazmat).
SATISFIES = {"H": {"H", "X"}, "N": {"N", "X"}, "T": {"T"}}


def required_endorsements(load: Load) -> list[str]:
    req = []
    if load.hazmat:
        req.append("H")
    if load.tank:
        req.append("N")
    if load.doubles:
        req.append("T")
    return req


def check_driver(driver: Driver, load: Load, tractor: Tractor | None, net: Network, rules: Rules,
                 plan_start: datetime, plan_end: datetime) -> tuple[list[dict], list[dict]]:
    """Returns (failures, trace)."""
    fails: list[dict] = []
    trace: list[dict] = []

    def rule(code: str, ok: bool, detail: str, **extra):
        trace.append({"rule": code, "citation": rules.cite(code), "subject": driver.id,
                      "status": "PASS" if ok else "FAIL", "detail": detail})
        if not ok:
            fails.append({"code": code, "driver_id": driver.id, "detail": detail, "citation": rules.cite(code), **extra})

    rule("CDL_CLASS", driver.cdl_class == "A", f"combination vehicle requires Class A; driver holds Class {driver.cdl_class}")
    for req in required_endorsements(load):
        ok = bool(SATISFIES[req] & set(driver.endorsements))
        rule("ENDORSEMENT_MISSING", ok, f"load requires endorsement {req}; driver has {driver.endorsements or 'none'}", endorsement=req)
    if tractor is not None:
        if "E" in driver.restrictions:
            rule("RESTRICTION", tractor.transmission != "manual", f"restriction E (no manual transmission); {tractor.id} is {tractor.transmission}")
        if {"L", "Z"} & set(driver.restrictions):
            rule("RESTRICTION", not tractor.air_brakes, f"air-brake restriction; {tractor.id} has air brakes")
    if "K" in driver.restrictions:
        interstate = net.loc(load.origin_id).state != net.loc(load.destination_id).state
        rule("RESTRICTION", not interstate, "restriction K (intrastate only) on an interstate load")
    rule("CDL_EXPIRED", driver.cdl_expires >= plan_end.date(), f"CDL valid through {driver.cdl_expires}")
    rule("MEDICAL_EXPIRED", driver.medical_expires >= plan_end.date(), f"medical certificate valid through {driver.medical_expires}")
    missing = [c for c in load.requires_credentials if c not in driver.credentials]
    rule("CREDENTIAL_MISSING", not missing, f"load requires {load.requires_credentials or 'no credentials'}; missing {missing or 'none'}")
    if driver.available_from and utc(driver.available_from) > utc(plan_start):
        rule("DRIVER_UNAVAILABLE", False, f"driver unavailable until {driver.available_from.isoformat()}")
    return fails, trace


def check_equipment(load: Load, tractor: Tractor | None, trailer: Trailer | None, rules: Rules,
                    plan_start: datetime, plan_end: datetime) -> tuple[list[dict], list[dict]]:
    fails: list[dict] = []
    trace: list[dict] = []

    def rule(code: str, subject: str, ok: bool, detail: str):
        trace.append({"rule": code, "citation": "company policy " + rules.company_id, "subject": subject,
                      "status": "PASS" if ok else "FAIL", "detail": detail})
        if not ok:
            fails.append({"code": code, "equipment_id": subject, "detail": detail})

    if trailer is not None:
        rule("EQUIPMENT_MISMATCH", trailer.id, trailer.type == load.equipment_type,
             f"load needs {load.equipment_type}; {trailer.id} is {trailer.type}")
        if load.hazmat:
            rule("EQUIPMENT_MISMATCH", trailer.id, trailer.hazmat_capable, f"{trailer.id} hazmat-capable={trailer.hazmat_capable}")
    for unit in (tractor, trailer):
        if unit is None:
            continue
        down = unit.status == "out_of_service" and (unit.out_of_service_until is None or utc(unit.out_of_service_until) > utc(plan_start))
        rule("EQUIPMENT_UNAVAILABLE", unit.id, not down, f"{unit.id} status {unit.status}")
        for blk in unit.maintenance:
            overlap = utc(blk.start) < utc(plan_end) and utc(blk.end) > utc(plan_start)
            rule("EQUIPMENT_UNAVAILABLE", unit.id, not overlap,
                 f"{unit.id} maintenance block {blk.start.isoformat()} to {blk.end.isoformat()}: {blk.reason}")
    if tractor is not None and load.hazmat:
        rule("EQUIPMENT_MISMATCH", tractor.id, tractor.hazmat_capable, f"{tractor.id} hazmat-capable={tractor.hazmat_capable}")
    return fails, trace
