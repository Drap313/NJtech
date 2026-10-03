"""Turns an assignment (plus optional recovery actions) into per-driver activity
timelines and simulates each one with the HOS engine.

Action types:
  CONTINUE                                        keep the committed plan
  RELAY {point_id, driver_id, tractor_id, mode}   second driver takes over at a relay point
  SWAP_DRIVER {driver_id, tractor_id, mode}       replacement driver takes the load before departure
  HOLD_FOR_RESET {point_id}                       current driver takes a 10-hour reset at a parking point
  SWAP_TRACTOR {tractor_id}                       replacement tractor brought to the driver
  REQUEST_NEW_APPOINTMENT {start, end}            ask the customer to move the delivery window
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..clock import minutes_between, utc
from ..compliance.hos import OFF, ON, Act, SimResult, simulate
from ..config import CostModel, Rules, get_cost_model, get_rules
from ..models import OFF_STATUSES, Assignment, Driver, Load, Window
from ..routing import Route
from ..state import Fleet


@dataclass
class Leg:
    driver: Driver
    role: str                 # primary | relay | replacement | released
    acts: list[Act]
    start: datetime | None    # simulation start (None = driver's ELD snapshot time)
    labor_from: datetime      # on-duty minutes after this count as plan labor
    tractor_id: str | None
    carries_cargo: bool
    sim: SimResult | None = None


@dataclass
class PlanBuild:
    kind: str
    actions: list[dict]
    title: str
    legs: list[Leg] = field(default_factory=list)
    marks: dict[str, datetime] = field(default_factory=dict)
    shuttle_miles: float = 0.0
    extra_empty_miles: float = 0.0
    handling: list[str] = field(default_factory=list)
    premiums: list[tuple[str, str]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cargo_tractor_id: str | None = None
    trailer_id: str | None = None
    delivery_window: Window | None = None
    appointment_change: bool = False
    decision_deadline: datetime | None = None


def committed_actions(asg: Assignment) -> list[dict]:
    if asg.committed:
        return asg.committed
    if asg.relay:
        r = asg.relay
        return [{"type": "RELAY", "point_id": r.point_id, "driver_id": r.driver_id, "tractor_id": r.tractor_id, "mode": r.mode}]
    if asg.hold:
        return [{"type": "HOLD_FOR_RESET", "point_id": asg.hold.point_id}]
    return [{"type": "CONTINUE"}]


class Planner:
    def __init__(self, fleet: Fleet, rules: Rules | None = None, cm: CostModel | None = None):
        self.f = fleet
        self.net = fleet.net
        self.R = rules or get_rules()
        self.cm = cm or get_cost_model()

    # ------------------------------------------------------------------ public
    def build(self, asg: Assignment, now: datetime, actions: list[dict] | None = None) -> PlanBuild:
        now = utc(now)
        actions = actions if actions is not None else committed_actions(asg)
        load = self.f.loads[asg.load_id]
        types = [a["type"] for a in actions]
        main = next((t for t in types if t != "REQUEST_NEW_APPOINTMENT"), "CONTINUE")
        pb = PlanBuild(kind=main, actions=actions, title="", trailer_id=asg.trailer_id,
                       cargo_tractor_id=asg.tractor_id, delivery_window=load.delivery_window)
        appt = next((a for a in actions if a["type"] == "REQUEST_NEW_APPOINTMENT"), None)
        if appt:
            pb.delivery_window = Window(start=appt["start"], end=appt["end"])
            pb.appointment_change = True
            pb.handling.append("appointment_change_cost")
            pb.assumptions.append("Customer accepts the requested appointment (requires approval before it is sent).")
            pb.kind = "HOLD_AND_REAPPOINT"
        act = next(a for a in actions if a["type"] == main) if main != "CONTINUE" else {"type": "CONTINUE"}
        if main != "SWAP_DRIVER" and (asg.driver_id is None or asg.status == "DECLINED"):
            pb.errors.append("No driver on the assignment; a replacement driver is required.")
            pb.title = "Continue unchanged"
            return pb
        handler = {"CONTINUE": self._continue, "RELAY": self._relay, "SWAP_DRIVER": self._swap,
                   "HOLD_FOR_RESET": self._hold, "SWAP_TRACTOR": self._swap_tractor}.get(main)
        if handler is None:
            pb.errors.append(f"Unsupported action {main}")
            return pb
        handler(pb, asg, load, act, now)
        if not pb.errors:
            for leg in pb.legs:
                if leg.sim is None:
                    leg.sim = self._sim(leg)
                for k, v in leg.sim.marks.items():
                    if leg.carries_cargo or k.startswith("relay") or k.startswith("swap"):
                        pb.marks.setdefault(k, v)
        return pb

    # ------------------------------------------------------------------ helpers
    def _sim(self, leg: Leg) -> SimResult:
        return simulate(leg.driver.eld, leg.acts, self.R, self.R.cycle_limit(leg.driver.cycle_profile), start=leg.start)

    def _name(self, loc_id: str) -> str:
        return self.net.loc(loc_id).name

    def _route(self, load: Load) -> Route:
        return self.net.routes[load.route_id]

    def _m0(self, asg: Assignment) -> int:
        return asg.progress.route_minutes_done if asg.progress.stage == "IN_TRANSIT" else 0

    def _load_ready(self, asg: Assignment, load: Load) -> datetime | None:
        p = asg.progress
        if p.stage == "AT_PICKUP" and p.stage_started_at:
            return utc(p.stage_started_at) + timedelta(minutes=load.load_minutes + asg.pickup_delay_minutes)
        return None

    def transit(self, route: Route, m_from: int, m_to: int, extra_first: int = 0, final_marks=()) -> list[Act]:
        acts: list[Act] = []
        for lo, hi, a, b in route.segments(m_from, m_to):
            mins = hi - lo + (extra_first if not acts else 0)
            mark = (f"pass:{b.loc}",) if hi == b.minutes else ()
            acts.append(Act("DRIVE", mins, label=f"{self._short(a.loc)} -> {self._short(b.loc)}",
                            a=self.net.route_point(route, lo), b=self.net.route_point(route, hi),
                            miles=round(route.miles_at(hi) - route.miles_at(lo), 1),
                            loc=b.loc if hi == b.minutes else None, route_from=lo, route_to=hi, marks_end=mark))
        if acts and final_marks:
            acts[-1].marks_end = acts[-1].marks_end + tuple(final_marks)
        return acts

    def _short(self, loc_id: str) -> str:
        return self.net.loc(loc_id).name.split(" (")[0]

    def delivery(self, load: Load, window: Window) -> list[Act]:
        dest = self.net.latlon(load.destination_id)
        return [
            Act("WAIT", until=utc(window.start), label="Wait for delivery appointment", stop="wait", loc=load.destination_id, a=dest),
            Act("ON", load.unload_minutes, label=f"Unload at {self._short(load.destination_id)}", stop="delivery",
                loc=load.destination_id, a=dest, marks_end=("delivered",)),
        ]

    def approach(self, d: Driver, dest_id: str, need_by: datetime, now: datetime, mode: str) -> tuple[list[Act], float, datetime, list[str]]:
        """Get driver d to dest_id by need_by, departing as late as possible."""
        miles, mins = self.net.travel(d.position, dest_id)
        is_off = d.eld.duty_status in OFF_STATUSES
        pretrip = self.cm.op("pretrip_minutes")
        # A long idle gap means the driver goes off duty and needs a fresh pre-trip.
        latest = utc(need_by) - timedelta(minutes=mins)
        going_off = is_off or minutes_between(now, latest) > self.cm.op("long_wait_goes_off_duty_minutes")
        start = max(now, latest - timedelta(minutes=pretrip if going_off else 0))
        if d.available_from and utc(d.available_from) > start:
            start = utc(d.available_from)
        wait = minutes_between(now, start)
        notes: list[str] = []
        acts: list[Act] = []
        here = (d.position.lat, d.position.lon)
        if wait > 0:
            acts.append(Act("WAIT", until=start, wait_status=OFF if going_off else ON, stop="wait", a=here,
                            label="Off duty until departure" if going_off else "Current duties until departure"))
            if going_off and not is_off:
                local = start.astimezone(ZoneInfo(self.net.loc(d.home_terminal).tz)).strftime("%H:%M %Z")
                notes.append(f"{d.id} is released from current duties and off duty until departure at {local} (other work not modeled).")
        if going_off:
            acts.append(Act("ON", pretrip, label="Pre-trip inspection", stop="pretrip", a=here))
        if mins > 0:
            dest = self.net.latlon(dest_id)
            if mode == "shuttle":
                acts.append(Act("ON", mins, label=f"Shuttle ride to {self._short(dest_id)}", stop="shuttle", a=here, b=dest, loc=dest_id))
            else:
                acts.append(Act("DRIVE", mins, label=f"Reposition to {self._short(dest_id)}", a=here, b=dest,
                                miles=miles, empty=True, loc=dest_id))
        return acts, miles, start, notes

    def cargo_pre(self, asg: Assignment, load: Load, d: Driver, t0: datetime, pb: PlanBuild) -> list[Act]:
        """Activities from the current holder's snapshot until the cargo is rolling."""
        p = asg.progress
        origin = self.net.latlon(load.origin_id)
        if p.stage == "PLANNED":
            acts, _, start, notes = self.approach(d, load.origin_id, load.pickup_window.start, t0, mode="drive")
            pb.assumptions += notes
            acts += [
                Act("WAIT", until=utc(load.pickup_window.start), label="Wait for pickup appointment", stop="wait",
                    loc=load.origin_id, a=origin, marks_start=("pickup_arrival",)),
                Act("ON", load.load_minutes + asg.pickup_delay_minutes, label=f"Check-in and loading at {self._short(load.origin_id)}",
                    stop="pickup", loc=load.origin_id, a=origin, marks_end=("depart_pickup",)),
            ]
            return acts
        if p.stage == "AT_PICKUP":
            pb.marks["pickup_arrival"] = utc(p.stage_started_at)
            ready = self._load_ready(asg, load)
            rem = max(0, int(round(minutes_between(t0, ready))))
            label = "Loading (remaining)" + (f", includes {asg.pickup_delay_minutes} min shipper delay" if asg.pickup_delay_minutes else "")
            return [Act("ON", rem, label=label, stop="pickup", loc=load.origin_id, a=origin, marks_end=("depart_pickup",))]
        if p.stage == "IN_TRANSIT":
            pb.marks["depart_pickup"] = utc(p.stage_started_at) if p.stage_started_at else t0
            if asg.enroute_delay_until and utc(asg.enroute_delay_until) > t0:
                rem = int(round(minutes_between(t0, utc(asg.enroute_delay_until))))
                pos = (d.position.lat, d.position.lon)
                return [Act("ON", rem, label="Reported en-route delay", stop="delay", a=pos)]
            return []
        return []

    def _tail(self, asg: Assignment, load: Load, pb: PlanBuild, m_from: int) -> list[Act]:
        route = self._route(load)
        return self.transit(route, m_from, route.total_minutes, extra_first=asg.route_extra_minutes,
                            final_marks=("delivery_arrival",)) + self.delivery(load, pb.delivery_window)

    def _primary(self, asg: Assignment) -> Driver:
        return self.f.drivers[asg.driver_id]

    def _call_in(self, pb: PlanBuild, d: Driver) -> None:
        if d.eld.duty_status in OFF_STATUSES:
            pb.premiums.append(("driver_call_in_premium", d.id))

    # ------------------------------------------------------------------ strategies
    def _continue(self, pb: PlanBuild, asg: Assignment, load: Load, act: dict, now: datetime) -> None:
        d = self._primary(asg)
        t0 = utc(d.eld.observed_at)
        pb.title = f"Continue unchanged with {d.id}"
        if asg.progress.stage == "AT_DELIVERY":
            started = utc(asg.progress.stage_started_at)
            pb.marks["delivery_arrival"] = started
            rem = max(0, int(round(minutes_between(t0, started + timedelta(minutes=load.unload_minutes)))))
            acts = [Act("ON", rem, label="Unloading (remaining)", stop="delivery", loc=load.destination_id,
                        a=self.net.latlon(load.destination_id), marks_end=("delivered",))]
        elif asg.progress.stage == "DELIVERED":
            acts = []
        else:
            acts = self.cargo_pre(asg, load, d, t0, pb) + self._tail(asg, load, pb, self._m0(asg))
        pb.legs.append(Leg(d, "primary", acts, None, t0, asg.tractor_id, True))

    def _relay(self, pb: PlanBuild, asg: Assignment, load: Load, act: dict, now: datetime) -> None:
        route = self._route(load)
        P, mp, m0 = act["point_id"], route.offset_of(act["point_id"]), self._m0(asg)
        d1, d2 = self._primary(asg), self.f.drivers[act["driver_id"]]
        pb.title = f"Relay to {d2.id} at {self.net.loc(P).name}"
        if mp is None or mp < m0 or (mp == m0 and not asg.relay):
            pb.errors.append(f"{P} is not ahead of the load on route {route.id}")
            return
        t0 = utc(d1.eld.observed_at)
        hand = self.cm.op("relay_handoff_minutes")
        mode = act.get("mode", "bobtail")
        pt = self.net.latlon(P)
        pre = self.cargo_pre(asg, load, d1, t0, pb)
        tail = self.transit(route, mp, route.total_minutes, final_marks=("delivery_arrival",)) + self.delivery(load, pb.delivery_window)
        tractor2 = act.get("tractor_id") if mode == "bobtail" else asg.tractor_id
        started = asg.relay.handoff_started_at if asg.relay and asg.relay.driver_id == d2.id else None
        if started:  # both drivers are at the lot and the handoff is under way
            done = utc(started) + timedelta(minutes=hand)
            p_acts = [Act("ON", max(0, int(round(minutes_between(t0, done)))), label=f"Relay handoff to {d2.id}", stop="handoff",
                          loc=P, a=pt, marks_end=("relay_handoff_done",))]
            r_acts = [Act("WAIT", until=done, label=f"Relay pickup from {d1.id}: hook {asg.trailer_id}", stop="handoff", loc=P, a=pt)] + tail
            pb.legs.append(Leg(d1, "primary", p_acts, None, t0, asg.tractor_id, True))
            pb.legs.append(Leg(d2, "relay", r_acts, now, now, tractor2, True))
            pb.cargo_tractor_id = tractor2
            pb.handling.append("relay_handling_cost")
            return
        if mp > m0:
            p_acts = pre + self.transit(route, m0, mp, extra_first=asg.route_extra_minutes, final_marks=("relay_arrival",))
            A = simulate(d1.eld, p_acts, self.R, self.R.cycle_limit(d1.cycle_profile)).marks["relay_arrival"]
        else:  # already at the relay point, waiting for the relay driver
            p_acts, A = list(pre), t0
            pb.marks["relay_arrival"] = utc(asg.progress.as_of or t0)
        r_acts, miles, start, notes = self.approach(d2, P, A, now, mode)
        pb.assumptions += notes
        probe = simulate(d2.eld, r_acts, self.R, self.R.cycle_limit(d2.cycle_profile), start=now)
        meet = max(A, probe.end.t)
        p_acts += [Act("WAIT", until=meet, label=f"Wait for {d2.id}", stop="wait", loc=P, a=pt),
                   Act("ON", hand, label=f"Relay handoff to {d2.id}", stop="handoff", loc=P, a=pt,
                       marks_start=("relay_meet",), marks_end=("relay_handoff_done",))]
        r_acts += [Act("WAIT", until=meet, label=f"Wait for {d1.id}", stop="wait", loc=P, a=pt),
                   Act("ON", hand, label=f"Relay pickup from {d1.id}: hook {asg.trailer_id}", stop="handoff", loc=P, a=pt)] + tail
        pb.legs.append(Leg(d1, "primary", p_acts, None, t0, asg.tractor_id, True))
        pb.legs.append(Leg(d2, "relay", r_acts, now, start, tractor2, True))
        if mode == "bobtail":
            pb.cargo_tractor_id = act.get("tractor_id")
            pb.assumptions.append(f"{d1.id} keeps {asg.tractor_id} and goes off duty at {self._short(P)}; parking available.")
        else:
            pb.shuttle_miles += miles * 2
            pb.assumptions.append(f"{d2.id} is shuttled to {self._short(P)} and slip-seats {asg.tractor_id}; {d1.id} rides the shuttle back.")
        pb.handling.append("relay_handling_cost")
        self._call_in(pb, d2)
        pb.decision_deadline = start
        if meet > A:
            pb.assumptions.append(f"{d1.id} waits {int(minutes_between(A, meet))} min at the relay point for {d2.id}.")

    def _swap(self, pb: PlanBuild, asg: Assignment, load: Load, act: dict, now: datetime) -> None:
        d2 = self.f.drivers[act["driver_id"]]
        mode = act.get("mode", "bobtail")
        pb.title = f"Swap to {d2.id} ({d2.name})"
        stage = asg.progress.stage
        origin_id = load.origin_id
        origin = self.net.latlon(origin_id)
        hand = self.cm.op("driver_swap_handoff_minutes")
        d1 = self.f.drivers.get(asg.driver_id) if asg.driver_id and asg.status != "DECLINED" else None
        if stage == "AT_PICKUP":
            ready = self._load_ready(asg, load)
            need = ready - timedelta(minutes=hand)
            r_acts, miles, start, notes = self.approach(d2, origin_id, need, now, mode)
            r_acts += [Act("WAIT", until=need, label="Wait at shipper", stop="wait", loc=origin_id, a=origin),
                       Act("ON", hand, label=f"Take over {asg.trailer_id} from {d1.id if d1 else 'yard'}", stop="handoff",
                           loc=origin_id, a=origin, marks_start=("swap_handoff_start",), marks_end=("swap_handoff_done",)),
                       Act("WAIT", until=ready, label="Loading completes", stop="pickup", loc=origin_id, a=origin, marks_end=("depart_pickup",))]
            pb.marks["pickup_arrival"] = utc(asg.progress.stage_started_at)
        elif stage == "PLANNED":
            r_acts, miles, start, notes = self.approach(d2, origin_id, load.pickup_window.start, now, mode)
            r_acts += [Act("WAIT", until=utc(load.pickup_window.start), label="Wait for pickup appointment", stop="wait",
                           loc=origin_id, a=origin, marks_start=("pickup_arrival",)),
                       Act("ON", load.load_minutes + asg.pickup_delay_minutes, label=f"Check-in and loading at {self._short(origin_id)}",
                           stop="pickup", loc=origin_id, a=origin, marks_end=("depart_pickup",))]
        else:
            pb.errors.append("Driver swap is only possible before departure; use a relay for a load in transit.")
            return
        pb.assumptions += notes
        r_acts += self._tail(asg, load, pb, 0)
        tractor = act.get("tractor_id") if mode == "bobtail" else asg.tractor_id
        pb.legs.append(Leg(d2, "replacement", r_acts, now, start, tractor, True))
        pb.cargo_tractor_id = tractor
        pb.decision_deadline = start
        self._call_in(pb, d2)
        if mode == "shuttle":
            pb.shuttle_miles += miles
            pb.assumptions.append(f"{d2.id} is shuttled to the shipper and slip-seats {asg.tractor_id}.")
        if d1 is not None and stage == "AT_PICKUP":
            probe = self._sim(pb.legs[-1])
            pb.legs[-1].sim = probe
            h_start = probe.marks.get("swap_handoff_start", now)
            home_miles, home_mins = self.net.travel(load.origin_id, d1.home_terminal)
            home = self.net.latlon(d1.home_terminal)
            rel = [Act("WAIT", until=h_start, label=f"Wait for {d2.id}", stop="wait", loc=origin_id, a=origin),
                   Act("ON", hand, label=f"Hand off load to {d2.id}", stop="handoff", loc=origin_id, a=origin)]
            if mode == "shuttle":
                rel.append(Act("ON", home_mins, label="Shuttle ride home", stop="shuttle", a=origin, b=home, loc=d1.home_terminal))
                pb.shuttle_miles += home_miles
            else:
                rel.append(Act("DRIVE", home_mins, label=f"Bobtail {asg.tractor_id} home", a=origin, b=home,
                               miles=home_miles, empty=True, loc=d1.home_terminal))
            pb.legs.insert(0, Leg(d1, "released", rel, None, utc(d1.eld.observed_at), asg.tractor_id, False))
        if d1 is not None and asg.status == "DISPATCHED":
            pb.premiums.append(("driver_reassignment_pay", d1.id))

    def _hold(self, pb: PlanBuild, asg: Assignment, load: Load, act: dict, now: datetime) -> None:
        route = self._route(load)
        d = self._primary(asg)
        t0 = utc(d.eld.observed_at)
        P = act["point_id"]
        m0 = self._m0(asg)
        mp = route.offset_of(P)
        reset = self.cm.op("reset_hold_minutes")
        pb.title = f"Hold {d.id} for 10-hour reset at {self.net.loc(P).name}"
        if mp is None or mp < m0:
            pb.errors.append(f"{P} is not ahead of the load")
            return
        pt = self.net.latlon(P)
        acts = self.cargo_pre(asg, load, d, t0, pb)
        acts += self.transit(route, m0, mp, extra_first=asg.route_extra_minutes)
        acts.append(Act("OFF", reset, label=f"10-hour reset at {self._short(P)}", stop="reset", loc=P, a=pt))
        acts += self.transit(route, mp, route.total_minutes, final_marks=("delivery_arrival",)) + self.delivery(load, pb.delivery_window)
        pb.legs.append(Leg(d, "primary", acts, None, t0, asg.tractor_id, True))
        pb.assumptions.append(f"Safe parking available at {self._short(P)} for the reset.")

    def _swap_tractor(self, pb: PlanBuild, asg: Assignment, load: Load, act: dict, now: datetime) -> None:
        d = self._primary(asg)
        t0 = utc(d.eld.observed_at)
        T = self.f.tractors[act["tractor_id"]]
        pb.title = f"Repower with {T.id}"
        miles, mins = self.net.travel(T.position, d.position)
        arrive = now + timedelta(minutes=mins + self.cm.op("tractor_swap_hook_minutes"))
        acts = self.cargo_pre(asg, load, d, t0, pb)
        pos = (d.position.lat, d.position.lon)
        acts.append(Act("WAIT", until=arrive, label=f"Wait for {T.id} and hook", stop="wait", a=pos))
        acts += self._tail(asg, load, pb, self._m0(asg))
        pb.legs.append(Leg(d, "primary", acts, None, t0, T.id, True))
        pb.cargo_tractor_id = T.id
        pb.extra_empty_miles += miles
        pb.handling.append("equipment_swap_cost")
        pb.assumptions.append(f"A yard driver brings {T.id} {miles:.0f} mi to {d.id} (yard driver time not modeled).")
