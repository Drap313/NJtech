"""
In-memory fleet state: the single source of truth the rest of the engine
reads and writes. v0.1 keeps this in memory, loaded from data/*.json at
startup -- swap for SQLite/Postgres later (see README) without changing any
module that calls FleetState methods, since they're the only access path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.models.entities import Driver, Vehicle, Trailer, Load, CustomerTerms, Assignment

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@dataclass
class FleetState:
    now: datetime
    drivers: dict[str, Driver] = field(default_factory=dict)
    vehicles: dict[str, Vehicle] = field(default_factory=dict)
    trailers: dict[str, Trailer] = field(default_factory=dict)
    loads: dict[str, Load] = field(default_factory=dict)
    customers: dict[str, CustomerTerms] = field(default_factory=dict)
    assignments: dict[str, Assignment] = field(default_factory=dict)
    terminals: list[str] = field(default_factory=list)
    incidents: dict[str, dict] = field(default_factory=dict)
    audit_log: list[dict] = field(default_factory=list)
    _next_incident_n: int = 0

    # -- lookups used throughout the engine --
    def driver(self, driver_id: str) -> Driver:
        return self.drivers[driver_id]

    def vehicle(self, vehicle_id: str) -> Vehicle:
        return self.vehicles[vehicle_id]

    def trailer(self, trailer_id: str) -> Trailer:
        return self.trailers[trailer_id]

    def load(self, load_id: str) -> Load:
        return self.loads[load_id]

    def available_drivers(self, exclude: set[str] | None = None) -> list[Driver]:
        exclude = exclude or set()
        return [d for d in self.drivers.values()
                if d.id not in exclude and d.duty_status not in ("off_duty",) or
                (d.id not in exclude and d.hours_since_last_reset >= 0)]

    def new_incident_id(self) -> str:
        self._next_incident_n += 1
        return f"INC-{self._next_incident_n:04d}"

    def append_audit(self, record: dict) -> None:
        record = dict(record)
        record["recorded_at"] = self.now.isoformat()
        self.audit_log.append(record)


def _load_json(name: str) -> list[dict]:
    with open(DATA_DIR / name) as f:
        return json.load(f)


def load_fleet_state(now: datetime | None = None) -> FleetState:
    """Build a fresh FleetState from data/*.json. Each call returns an
    independent, mutable copy -- call this once per test / once per process,
    not on every request, unless you want everyone sharing the same state
    (the API layer does want that; tests usually don't)."""
    now = now or datetime.fromisoformat("2026-10-03T02:00:00")
    state = FleetState(now=now)

    for row in _load_json("drivers.json"):
        row = dict(row)
        row["last_eld_sync"] = _parse_dt(row["last_eld_sync"])
        row["last_location_update"] = _parse_dt(row["last_location_update"])
        d = Driver(**row)
        state.drivers[d.id] = d

    for row in _load_json("vehicles.json"):
        row = dict(row)
        row["last_location_update"] = _parse_dt(row["last_location_update"])
        v = Vehicle(**row)
        state.vehicles[v.id] = v

    for row in _load_json("trailers.json"):
        t = Trailer(**row)
        state.trailers[t.id] = t

    for row in _load_json("customer_terms.json"):
        c = CustomerTerms(**row)
        state.customers[c.customer_id] = c

    for row in _load_json("loads.json"):
        row = dict(row)
        for k in ("pickup_window_start", "pickup_window_end",
                  "delivery_window_start", "delivery_window_end"):
            row[k] = _parse_dt(row[k])
        ld = Load(**row)
        state.loads[ld.id] = ld
        if ld.assigned_driver:
            state.assignments[f"A-{ld.id}"] = Assignment(
                id=f"A-{ld.id}",
                load_id=ld.id,
                driver_id=ld.assigned_driver,
                vehicle_id=ld.assigned_vehicle,
                trailer_id=ld.assigned_trailer,
                planned_start=row["pickup_window_start"],
                status="active" if ld.status == "active" else "planned",
            )

    state.terminals = ["Pecos Yard", "Midland Terminal"]
    return state
