"""Canonical fleet records. Every operational record carries a source and an
observed timestamp so freshness can be judged explicitly."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DutyStatus(str, Enum):
    OFF_DUTY = "OFF_DUTY"
    SLEEPER = "SLEEPER"
    DRIVING = "DRIVING"
    ON_DUTY = "ON_DUTY"


OFF_STATUSES = (DutyStatus.OFF_DUTY, DutyStatus.SLEEPER)


class Position(BaseModel):
    lat: float
    lon: float
    location_id: str | None = None
    observed_at: datetime
    source: str = "gps"


class EldSnapshot(BaseModel):
    observed_at: datetime
    source: str = "eld"
    duty_status: DutyStatus
    status_since: datetime
    shift_start: datetime | None = None
    drive_minutes_in_shift: int = 0
    drive_minutes_since_break: int = 0
    cycle_used_minutes: int = 0


class ShiftRecord(BaseModel):
    start: datetime
    end: datetime


class Driver(BaseModel):
    id: str
    name: str
    home_terminal: str
    cdl_class: Literal["A", "B", "C"]
    endorsements: list[str] = []
    restrictions: list[str] = []
    credentials: list[str] = []
    cdl_expires: date
    medical_expires: date
    cycle_profile: Literal["70_8", "60_7"] = "70_8"
    availability: Literal["available", "assigned", "unavailable"] = "available"
    available_from: datetime | None = None
    tractor_id: str | None = None
    position: Position
    eld: EldSnapshot
    feed: Literal["ok", "broken"] = "ok"
    claimed_exceptions: list[str] = []
    recent_shifts: list[ShiftRecord] = []
    last_confirmation_at: datetime | None = None
    last_schedule_change_at: datetime | None = None
    stale_flagged_at: datetime | None = None
    notes: str = ""


class Block(BaseModel):
    start: datetime
    end: datetime
    reason: str


class Tractor(BaseModel):
    id: str
    kind: str = "sleeper"
    transmission: Literal["automatic", "manual"] = "automatic"
    air_brakes: bool = True
    hazmat_capable: bool = False
    status: Literal["available", "in_use", "out_of_service"] = "available"
    out_of_service_until: datetime | None = None
    position: Position
    maintenance: list[Block] = []


class Trailer(BaseModel):
    id: str
    type: Literal["dry_van", "reefer", "flatbed", "tanker"]
    length_ft: int = 53
    hazmat_capable: bool = False
    status: Literal["available", "in_use", "out_of_service"] = "available"
    out_of_service_until: datetime | None = None
    position: Position
    maintenance: list[Block] = []


class Window(BaseModel):
    start: datetime
    end: datetime


class CustomerTerms(BaseModel):
    detention_free_minutes: int = 120
    detention_rate_per_hour: float = 60
    late_flat: float = 0
    late_per_hour: float = 0
    late_cap: float = 0
    reschedule_allowed: bool = False


class Customer(BaseModel):
    id: str
    name: str
    terms: CustomerTerms


class Location(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    tz: str
    state: str
    kind: str
    parking: bool = False
    relay: bool = False


class Load(BaseModel):
    id: str
    customer_id: str
    origin_id: str
    destination_id: str
    route_id: str
    commodity: str = ""
    pickup_window: Window
    delivery_window: Window
    load_minutes: int
    unload_minutes: int
    planned_miles: float
    planned_drive_minutes: int
    equipment_type: str
    hazmat: bool = False
    tank: bool = False
    doubles: bool = False
    requires_credentials: list[str] = []
    revenue: float = 0
    priority: Literal["low", "normal", "high"] = "normal"
    status: Literal["proposed", "active", "delivered", "cancelled"] = "active"
    customer_messages: list[dict] = []


Stage = Literal["PLANNED", "AT_PICKUP", "IN_TRANSIT", "AT_DELIVERY", "DELIVERED"]


class Progress(BaseModel):
    stage: Stage = "PLANNED"
    stage_started_at: datetime | None = None
    route_minutes_done: int = 0
    as_of: datetime | None = None


class Relay(BaseModel):
    point_id: str
    driver_id: str
    tractor_id: str | None = None
    mode: Literal["bobtail", "shuttle"] = "bobtail"
    handoff_started_at: datetime | None = None


class Hold(BaseModel):
    point_id: str
    minutes: int = 600


AssignmentStatus = Literal["PROPOSED", "BLOCKED", "DISPATCHED", "DECLINED", "COMPLETED", "CANCELLED"]


class Assignment(BaseModel):
    id: str
    load_id: str
    driver_id: str | None
    tractor_id: str | None
    trailer_id: str | None
    status: AssignmentStatus
    progress: Progress = Field(default_factory=Progress)
    pickup_delay_minutes: int = 0
    enroute_delay_until: datetime | None = None
    route_extra_minutes: int = 0
    relay: Relay | None = None
    hold: Hold | None = None
    committed: list[dict] | None = None   # executed recovery actions still in progress (e.g. a swap before handoff)
    driver_confirmed_at: datetime | None = None
    declined_by: list[str] = []
    revision: int = 1
    history: list[dict] = []


EventType = Literal[
    "LOAD_CREATED", "ASSIGNMENT_PROPOSED", "ASSIGNMENT_APPROVED", "DRIVER_ACCEPTED", "DRIVER_DECLINED",
    "DRIVER_STATUS_CHANGED", "DRIVER_DELAY_REPORTED", "ELD_UPDATED", "GPS_UPDATED", "PICKUP_STARTED",
    "PICKUP_DELAYED", "PICKUP_COMPLETED", "DELIVERY_WINDOW_CHANGED", "VEHICLE_UNAVAILABLE",
    "TRAILER_UNAVAILABLE", "ROUTE_TIME_CHANGED", "CUSTOMER_MESSAGE_RECEIVED", "DATA_BECAME_STALE",
    "ARRIVED_AT_DELIVERY", "DELIVERY_COMPLETED", "REFORECAST",
]


class Event(BaseModel):
    event_id: str
    type: EventType
    entity_type: str
    entity_id: str
    occurred_at: datetime
    received_at: datetime
    source: str
    payload: dict[str, Any] = {}
    idempotency_key: str
