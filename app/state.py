"""In-memory view of canonical fleet state loaded from the store."""
from __future__ import annotations

from dataclasses import dataclass

from .db import Store
from .models import Assignment, Customer, Driver, Load, Tractor, Trailer
from .routing import Network, get_network

KINDS = {"driver": Driver, "tractor": Tractor, "trailer": Trailer, "load": Load, "assignment": Assignment, "customer": Customer}


@dataclass
class Fleet:
    drivers: dict[str, Driver]
    tractors: dict[str, Tractor]
    trailers: dict[str, Trailer]
    loads: dict[str, Load]
    assignments: dict[str, Assignment]
    customers: dict[str, Customer]
    net: Network

    @classmethod
    def load(cls, store: Store) -> "Fleet":
        def grab(kind):
            m = KINDS[kind]
            return {d["id"]: m.model_validate(d) for d in store.all(kind)}
        return cls(grab("driver"), grab("tractor"), grab("trailer"), grab("load"), grab("assignment"), grab("customer"), get_network())

    def save(self, store: Store, obj) -> None:
        kind = {Driver: "driver", Tractor: "tractor", Trailer: "trailer", Load: "load", Assignment: "assignment", Customer: "customer"}[type(obj)]
        store.put(kind, obj.id, obj)

    def assignment_for_driver(self, driver_id: str) -> Assignment | None:
        for a in self.assignments.values():
            pending = {x.get("driver_id") for x in (a.committed or [])}
            if a.status == "DISPATCHED" and (a.driver_id == driver_id or (a.relay and a.relay.driver_id == driver_id) or driver_id in pending):
                return a
        return None

    def assignments_for_load(self, load_id: str) -> list[Assignment]:
        return [a for a in self.assignments.values() if a.load_id == load_id and a.status not in ("CANCELLED",)]

    def active_assignments(self) -> list[Assignment]:
        return [a for a in sorted(self.assignments.values(), key=lambda a: a.id) if a.status == "DISPATCHED"]
