"""Always-on loop plus the mock fleet.

The mock fleet treats each active plan's simulated timeline as ground truth:
every few simulated minutes it emits ELD/GPS telemetry and stage events for
every driver whose feed works, so the monitoring loop sees a living fleet.
Feeds marked "broken" stop reporting and go stale on their own.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from ..clock import minutes_between, utc
from ..compliance.hos import DRIVING, OFF, simulate, state_at
from ..config import get_rules
from ..forecasting.planner import Planner
from ..models import OFF_STATUSES
from ..state import Fleet
from .processor import Engine, make_event

log = logging.getLogger("dispatch.runtime")
STAGE_EVENTS = [("pickup_arrival", "PICKUP_STARTED", "AT_PICKUP"), ("depart_pickup", "PICKUP_COMPLETED", "IN_TRANSIT"),
                ("delivery_arrival", "ARRIVED_AT_DELIVERY", "AT_DELIVERY"), ("delivered", "DELIVERY_COMPLETED", "DELIVERED")]
STAGE_ORDER = ["PLANNED", "AT_PICKUP", "IN_TRANSIT", "AT_DELIVERY", "DELIVERED"]


class FleetRuntime:
    def __init__(self, engine: Engine, telemetry_minutes: float = 5.0, reforecast_minutes: float = 1.0, tick_seconds: float = 1.0):
        self.engine = engine
        self.store = engine.store
        self.clock = engine.clock
        self.telemetry_minutes = telemetry_minutes
        self.reforecast_minutes = reforecast_minutes
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.ticks = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="fleet-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = None
            except Exception as e:  # keep monitoring even if one tick fails
                log.exception("runtime tick failed")
                self.last_error = repr(e)
            self._stop.wait(self.tick_seconds)

    def _due(self, key: str, every_min: float, now: datetime) -> bool:
        last = self.store.meta_get(key)
        if last is not None and minutes_between(datetime.fromisoformat(last), now) < every_min and datetime.fromisoformat(last) <= now:
            return False
        self.store.meta_set(key, now.isoformat())
        return True

    def tick(self) -> None:
        self.ticks += 1
        now = self.clock.now()
        if self.store.meta_get("clock", {}).get("paused"):
            return
        now = now.replace(second=0)  # whole sim minutes keep the projection free of rounding drift
        prev = self.store.meta_get("last_telemetry")
        if self._due("last_telemetry", self.telemetry_minutes, now):
            # Plan from the previous report and read the state at now, so every leg advances.
            plan_time = datetime.fromisoformat(prev) if prev and datetime.fromisoformat(prev) < now else now
            self.telemetry(plan_time, now)
        self.stale_check(now)
        if self._due("last_reforecast", self.reforecast_minutes, now):
            self.engine.sweep()

    # ------------------------------------------------------------------ feeds
    def stale_check(self, now: datetime) -> None:
        R = get_rules()
        fleet = Fleet.load(self.store)
        for d in fleet.drivers.values():
            if d.stale_flagged_at or not fleet.assignment_for_driver(d.id):
                continue
            age = minutes_between(utc(d.eld.observed_at), now)
            if age > R.max_eld_age:
                self.engine.process_event(make_event("DATA_BECAME_STALE", "driver", d.id, {"age_minutes": round(age)},
                                                     "monitor:feed-watchdog", now, idempotency_key=f"stale:{d.id}:{d.eld.observed_at.isoformat()}"))

    def telemetry(self, plan_time: datetime, now: datetime) -> None:
        with self.store.lock:
            fleet = Fleet.load(self.store)
            planner = Planner(fleet)
            covered: set[str] = set()
            for asg in fleet.active_assignments():
                pb = planner.build(asg, plan_time)
                if pb.errors:
                    continue
                if all(l.driver.feed == "ok" for l in pb.legs if l.carries_cargo):
                    self._stage_events(fleet, asg, pb, now)  # a dead feed reports nothing, including stage changes
                route = fleet.net.routes[fleet.loads[asg.load_id].route_id]
                done = _route_done(pb, now)
                reset_end = max((e.end for l in pb.legs if l.sim for e in l.sim.resets), default=None)
                for leg in pb.legs:
                    d = leg.driver
                    covered.add(d.id)
                    if d.feed != "ok" or leg.sim is None:
                        continue
                    upd = {}
                    if leg.carries_cargo and done is not None:
                        upd["route_minutes_done"] = int(round(min(done, route.total_minutes)))
                    if asg.relay and leg.role == "primary":
                        if not asg.relay.handoff_started_at and "relay_meet" in pb.marks and now >= pb.marks["relay_meet"]:
                            upd["relay_handoff_started_at"] = pb.marks["relay_meet"].isoformat()
                        if "relay_handoff_done" in pb.marks and now >= pb.marks["relay_handoff_done"]:
                            upd["complete_relay"] = True
                    if asg.committed and leg.role == "replacement" and "swap_handoff_done" in pb.marks and now >= pb.marks["swap_handoff_done"]:
                        upd["complete_swap"] = True
                    if asg.hold and reset_end and now >= reset_end:
                        upd["clear_hold"] = True
                    self._emit_driver(d, leg.sim, now, upd, fleet)
            for d in fleet.drivers.values():
                if d.id in covered or d.feed != "ok":
                    continue
                self._emit_idle(d, now)

    def _stage_events(self, fleet: Fleet, asg, pb, now: datetime) -> None:
        cur = STAGE_ORDER.index(asg.progress.stage)
        for mark, etype, stage in STAGE_EVENTS:
            t = pb.marks.get(mark)
            if t is None or t > now or STAGE_ORDER.index(stage) <= cur:
                continue
            self.engine.process_event(make_event(etype, "assignment", asg.id, {"mark": mark}, "mock-fleet:telematics", t,
                                                 received_at=now, idempotency_key=f"{etype}:{asg.id}:{asg.revision}"))
            cur = STAGE_ORDER.index(stage)

    def _emit_driver(self, d, sim, now: datetime, upd: dict, fleet: Fleet) -> None:
        c, e, since = state_at(sim, now)
        if e is not None and e.a is not None:
            if e.status == DRIVING and e.b is not None:
                f = max(0.0, min(1.0, minutes_between(e.start, now) / max(1.0, minutes_between(e.start, e.end))))
                lat, lon = e.a[0] + f * (e.b[0] - e.a[0]), e.a[1] + f * (e.b[1] - e.a[1])
            elif now >= e.end and e.b is not None:
                lat, lon = e.b
            else:
                lat, lon = e.a
        else:
            lat, lon = d.position.lat, d.position.lon
        status = "OFF_DUTY" if c.status == OFF else c.status
        payload = {
            "eld": {"observed_at": now.isoformat(), "source": d.eld.source, "duty_status": status,
                    "status_since": (since or utc(d.eld.status_since)).isoformat(),
                    "shift_start": c.shift_start.isoformat() if c.shift_start else None,
                    "drive_minutes_in_shift": c.drive, "drive_minutes_since_break": c.since_break, "cycle_used_minutes": c.cycle},
            "position": {"lat": round(lat, 5), "lon": round(lon, 5), "observed_at": now.isoformat(), "source": d.position.source,
                         "location_id": fleet.net.nearest_location(lat, lon)},
        }
        if upd:
            payload["assignment_update"] = upd
        self.engine.process_event(make_event("ELD_UPDATED", "driver", d.id, payload, d.eld.source, now))

    def _emit_idle(self, d, now: datetime) -> None:
        R = get_rules()
        res = simulate(d.eld, [], R, R.cycle_limit(d.cycle_profile), start=now)
        c = res.end
        status = "OFF_DUTY" if d.eld.duty_status in OFF_STATUSES else "ON_DUTY"
        since = d.eld.status_since
        if status == "ON_DUTY" and c.shift_start and minutes_between(c.shift_start, now) >= R.window:
            status, since = "OFF_DUTY", now  # idle local work ends with the duty window
        payload = {"eld": {"observed_at": now.isoformat(), "source": d.eld.source, "duty_status": status,
                           "status_since": utc(since).isoformat(),
                           "shift_start": c.shift_start.isoformat() if c.shift_start else None,
                           "drive_minutes_in_shift": c.drive, "drive_minutes_since_break": c.since_break, "cycle_used_minutes": c.cycle},
                   "position": d.position.model_copy(update={"observed_at": now}).model_dump(mode="json")}
        self.engine.process_event(make_event("ELD_UPDATED", "driver", d.id, payload, d.eld.source, now))


def _route_done(pb, now: datetime) -> float | None:
    best = None
    for leg in pb.legs:
        if not leg.carries_cargo or not leg.sim:
            continue
        for e in leg.sim.entries:
            if e.route_from is None or e.start > now:
                continue
            if e.end <= now:
                v = e.route_to
            else:
                f = minutes_between(e.start, now) / max(1.0, minutes_between(e.start, e.end))
                v = e.route_from + f * (e.route_to - e.route_from)
            best = v if best is None else max(best, v)
    return best
