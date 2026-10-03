"""Deterministic routing: explicit loaded routes with waypoint offsets, explicit
pairs for common repositioning moves, and a great-circle fallback."""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from .config import SYNTHETIC_DIR, load_yaml
from .models import Location, Position

ROAD_FACTOR = 1.2
FALLBACK_MPH = 50.0


@dataclass(frozen=True)
class Waypoint:
    loc: str
    miles: float
    minutes: int


@dataclass(frozen=True)
class Route:
    id: str
    waypoints: tuple[Waypoint, ...]

    @property
    def total_minutes(self) -> int:
        return self.waypoints[-1].minutes

    @property
    def total_miles(self) -> float:
        return self.waypoints[-1].miles

    def offset_of(self, loc_id: str) -> int | None:
        for w in self.waypoints:
            if w.loc == loc_id:
                return w.minutes
        return None

    def miles_at(self, minute: float) -> float:
        for a, b in zip(self.waypoints, self.waypoints[1:]):
            if a.minutes <= minute <= b.minutes:
                f = (minute - a.minutes) / max(1, b.minutes - a.minutes)
                return a.miles + f * (b.miles - a.miles)
        return self.total_miles if minute > 0 else 0.0

    def segments(self, m_from: int, m_to: int) -> list[tuple[int, int, Waypoint, Waypoint]]:
        """Pieces of the route between two minute offsets, split at waypoints."""
        out = []
        for a, b in zip(self.waypoints, self.waypoints[1:]):
            lo, hi = max(a.minutes, m_from), min(b.minutes, m_to)
            if hi > lo:
                out.append((lo, hi, a, b))
        return out


class Network:
    def __init__(self, raw: dict):
        self.locations: dict[str, Location] = {k: Location(id=k, **v) for k, v in raw["locations"].items()}
        self.pairs: dict[tuple[str, str], tuple[float, int]] = {}
        for p in raw.get("pairs", []):
            self.pairs[(p["a"], p["b"])] = (p["miles"], p["minutes"])
            self.pairs[(p["b"], p["a"])] = (p["miles"], p["minutes"])
        self.routes: dict[str, Route] = {
            rid: Route(rid, tuple(Waypoint(w["loc"], float(w["miles"]), int(w["minutes"])) for w in wps))
            for rid, wps in raw["routes"].items()
        }

    def loc(self, id: str) -> Location:
        return self.locations[id]

    def latlon(self, ref: str | Position | tuple[float, float]) -> tuple[float, float]:
        if isinstance(ref, str):
            l = self.locations[ref]
            return (l.lat, l.lon)
        if isinstance(ref, Position):
            return (ref.lat, ref.lon)
        return ref

    def travel(self, a: str | Position, b: str | Position) -> tuple[float, int]:
        """(miles, minutes) for a non-route move."""
        ida = a if isinstance(a, str) else a.location_id
        idb = b if isinstance(b, str) else b.location_id
        if ida and idb:
            if ida == idb:
                return (0.0, 0)
            if (ida, idb) in self.pairs:
                return self.pairs[(ida, idb)]
        (la, oa), (lb, ob) = self.latlon(a), self.latlon(b)
        miles = haversine_miles(la, oa, lb, ob) * ROAD_FACTOR
        if miles < 0.5:
            return (0.0, 0)
        return (round(miles, 1), int(math.ceil(miles / FALLBACK_MPH * 60)))

    def route_point(self, route: Route, minute: float) -> tuple[float, float]:
        for a, b in zip(route.waypoints, route.waypoints[1:]):
            if a.minutes <= minute <= b.minutes:
                f = (minute - a.minutes) / max(1, b.minutes - a.minutes)
                pa, pb = self.latlon(a.loc), self.latlon(b.loc)
                return (pa[0] + f * (pb[0] - pa[0]), pa[1] + f * (pb[1] - pa[1]))
        return self.latlon(route.waypoints[-1].loc if minute > 0 else route.waypoints[0].loc)

    def nearest_location(self, lat: float, lon: float, max_miles: float = 2.0) -> str | None:
        best, best_d = None, max_miles
        for l in self.locations.values():
            d = haversine_miles(lat, lon, l.lat, l.lon)
            if d <= best_d:
                best, best_d = l.id, d
        return best


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@lru_cache
def get_network() -> Network:
    return Network(load_yaml(SYNTHETIC_DIR / "network.yaml"))
