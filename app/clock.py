"""Simulation clock. The demo runs on simulated time anchored to wall time with
an adjustable speed; deterministic replays pin it to each event's timestamp."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from .db import Store


def utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime not allowed; every timestamp needs a zone")
    return dt.astimezone(timezone.utc)


def minutes_between(a: datetime, b: datetime) -> float:
    """b - a in minutes."""
    return (b - a).total_seconds() / 60.0


class SimClock:
    def __init__(self, store: Store):
        self.store = store

    def _state(self) -> dict:
        return self.store.meta_get("clock") or {"anchor": datetime.now(timezone.utc).isoformat(), "wall": time.time(), "speed": 1.0, "paused": False}

    def now(self) -> datetime:
        s = self._state()
        anchor = datetime.fromisoformat(s["anchor"])
        if s["paused"]:
            return anchor
        return (anchor + timedelta(seconds=(time.time() - s["wall"]) * s["speed"])).replace(microsecond=0)

    def set(self, t: datetime | None = None, speed: float | None = None, paused: bool | None = None) -> dict:
        s = self._state()
        current = self.now()
        s = {
            "anchor": utc(t if t is not None else current).isoformat(),
            "wall": time.time(),
            "speed": float(speed if speed is not None else s["speed"]),
            "paused": bool(paused if paused is not None else s["paused"]),
        }
        self.store.meta_set("clock", s)
        return self.status()

    def advance(self, minutes: float) -> dict:
        return self.set(self.now() + timedelta(minutes=minutes))

    def status(self) -> dict:
        s = self._state()
        return {"now": self.now().isoformat(), "speed": s["speed"], "paused": s["paused"]}
