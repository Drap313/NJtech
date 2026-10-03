"""
Toy routing layer.

There is no real map here -- just a distance table between the named
locations used in the synthetic fleet (terminals, customer sites, and a few
waypoints usable as relay points), plus a flat average speed. This is a
deliberate simplification for a hackathon prototype: swap this module out for
a real routing API (or an offline road-graph) without touching anything else,
since every other module only calls distance_miles() / eta_minutes().
"""
from __future__ import annotations

AVG_SPEED_MPH = 50.0

# Symmetric distance table, miles. Add a location once; both directions work.
_DISTANCES: dict[tuple[str, str], float] = {
    ("Pecos Yard", "Sterling City Terminal"): 185,
    ("Pecos Yard", "Big Lake Yard"): 92,
    ("Big Lake Yard", "Sterling City Terminal"): 93,
    ("Pecos Yard", "Carlsbad Plant"): 120,
    ("Pecos Yard", "Odessa"): 82,
    ("Midland Terminal", "Odessa"): 20,
    ("Midland Terminal", "Pecos Yard"): 105,
    ("Midland Terminal", "Big Spring Station"): 40,
    ("Midland Terminal", "Big Lake Yard"): 68,
    ("Odessa", "Big Lake Yard"): 65,
    ("Odessa", "Carlsbad Plant"): 145,
    ("Odessa", "Sterling City Terminal"): 95,
    ("Big Spring Station", "Sterling City Terminal"): 60,
    ("Big Spring Station", "Big Lake Yard"): 75,
    ("Monahans", "Pecos Yard"): 35,
    ("Monahans", "Odessa"): 35,
    ("Monahans", "Midland Terminal"): 55,
    ("Hartford", "Boston"): 100,
    ("Hartford", "Springfield"): 28,
    ("Springfield", "Albany"): 90,
    ("Boston", "Springfield"): 90,
    ("Boston", "Albany"): 170,
}


def distance_miles(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if (a, b) in _DISTANCES:
        return _DISTANCES[(a, b)]
    if (b, a) in _DISTANCES:
        return _DISTANCES[(b, a)]
    raise KeyError(
        f"No distance known between {a!r} and {b!r}. "
        f"Add it to app/routing.py:_DISTANCES."
    )


def eta_minutes(a: str, b: str, speed_mph: float = AVG_SPEED_MPH) -> int:
    miles = distance_miles(a, b)
    return round(miles / speed_mph * 60)


def driving_minutes_for(miles: float, speed_mph: float = AVG_SPEED_MPH) -> int:
    return round(miles / speed_mph * 60)
