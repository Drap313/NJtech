"""
Core data models for Dispatch Guardian.

Plain dataclasses on purpose: the engine underneath has to stay readable and
dependency-light. FastAPI/pydantic only live at the API boundary (see app/api/).

Time convention: every "remaining" field (drive_minutes_remaining,
shift_minutes_remaining, cycle_minutes_remaining) is a current snapshot value,
maintained by whatever updates the fleet state (an ELD feed, in a real
deployment). v0.1 does NOT recompute these from duty_history -- duty_history
is stored for the audit trail but not replayed. That's a deliberate scope cut,
not an oversight: see README "Known simplifications".
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


def _now_default() -> datetime:
    return datetime.utcnow()


@dataclass
class Driver:
    id: str
    name: str
    current_location: str
    duty_status: str  # "driving" | "on_duty_not_driving" | "off_duty" | "sleeper_berth"
    drive_minutes_remaining: int          # of the 11-hour driving limit
    shift_minutes_remaining: int          # of the 14-hour window
    cycle_minutes_remaining: int          # of the configured 60/70-hour cycle
    hours_since_last_reset: float         # consecutive hours off duty, most recent break
    minutes_since_last_break: int         # driving minutes accumulated since the last 30-min break
    cdl_class: str                        # "A" | "B" | "C"
    endorsements: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)
    available_at: Optional[str] = None    # ISO timestamp, None = available now
    home_terminal: str = ""
    consecutive_overnight_assignments: int = 0
    reported_status: str = "normal"       # "normal" | "needs_rest"
    last_eld_sync: datetime = field(default_factory=_now_default)
    last_location_update: datetime = field(default_factory=_now_default)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["last_eld_sync"] = self.last_eld_sync.isoformat()
        d["last_location_update"] = self.last_location_update.isoformat()
        return d


@dataclass
class Vehicle:
    id: str
    type: str                              # "tractor"
    make: str
    model: str
    current_location: str
    status: str                            # "available" | "assigned" | "shop"
    assigned_driver: Optional[str] = None
    gross_weight_class: str = "Class 8"
    equipment_capabilities: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)
    available_at: Optional[str] = None
    last_location_update: datetime = field(default_factory=_now_default)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["last_location_update"] = self.last_location_update.isoformat()
        return d


@dataclass
class Trailer:
    id: str
    type: str                              # "dry_van" | "reefer" | "flatbed" | "tanker" | "container_chassis" | "specialized"
    current_location: str
    status: str                            # "available" | "assigned" | "shop"
    capabilities: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)
    available_at: Optional[str] = None
    spec: dict = field(default_factory=dict)   # free-form: gallons, compartments, gvwr_lb, etc.

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CustomerTerms:
    customer_id: str
    name: str
    grace_period_minutes: int
    late_penalty_per_hour: float
    appointment_change_policy: str
    notification_requirement_minutes: int
    contact_name: str
    contact_phone: str
    special_site_requirements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Load:
    id: str
    customer: str                          # customer_id, references CustomerTerms
    origin: str
    destination: str
    pickup_window_start: datetime
    pickup_window_end: datetime
    delivery_window_start: datetime
    delivery_window_end: datetime
    estimated_loading_minutes: int
    estimated_unloading_minutes: int
    cargo_type: str
    weight_lb: int
    required_trailer_type: str
    required_vehicle_capabilities: list[str] = field(default_factory=list)
    required_driver_endorsements: list[str] = field(default_factory=list)
    revenue: float = 0.0
    late_penalty_per_hour: float = 0.0
    unserved_penalty: float = 0.0
    status: str = "proposed"               # "proposed" | "active" | "delivered" | "unassigned"
    assigned_driver: Optional[str] = None
    assigned_vehicle: Optional[str] = None
    assigned_trailer: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("pickup_window_start", "pickup_window_end",
                  "delivery_window_start", "delivery_window_end"):
            d[k] = getattr(self, k).isoformat()
        return d


@dataclass
class Assignment:
    id: str
    load_id: str
    driver_id: str
    vehicle_id: str
    trailer_id: str
    planned_start: datetime
    planned_completion: Optional[datetime] = None
    planned_stops: list[str] = field(default_factory=list)
    status: str = "planned"                # "planned" | "active" | "completed" | "superseded"
    created_by: str = "system"
    approved_by: Optional[str] = None
    source: str = "synthetic_fleet"
    last_updated_at: datetime = field(default_factory=_now_default)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["planned_start"] = self.planned_start.isoformat()
        d["planned_completion"] = self.planned_completion.isoformat() if self.planned_completion else None
        d["last_updated_at"] = self.last_updated_at.isoformat()
        return d
