"""Deterministic hours-of-service projection.

A driver plan is a list of activities (drive, on-duty, off-duty, wait). The
simulator walks it minute-accurately from an ELD snapshot, inserts required
30-minute breaks, applies 10-hour resets and 34-hour restarts, and records
every limit it would cross (with excess minutes and the first moment of
failure). It never decides anything by heuristic: limits come from Rules.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..clock import minutes_between, utc
from ..config import Rules
from ..models import EldSnapshot

DRIVING, ON, OFF = "DRIVING", "ON_DUTY", "OFF_DUTY"


@dataclass
class Act:
    kind: str                       # DRIVE | ON | OFF | WAIT
    minutes: int = 0
    until: datetime | None = None   # WAIT only
    wait_status: str = ON           # WAIT only: ON or OFF
    label: str = ""
    stop: str | None = None         # pretrip|pickup|delivery|handoff|reset|break|shuttle|wait|delay|status
    loc: str | None = None
    a: tuple[float, float] | None = None
    b: tuple[float, float] | None = None
    miles: float = 0.0
    empty: bool = False
    route_from: int | None = None
    route_to: int | None = None
    marks_start: tuple[str, ...] = ()
    marks_end: tuple[str, ...] = ()


@dataclass
class Clocks:
    t: datetime
    status: str
    shift_start: datetime | None
    drive: int
    since_break: int
    nondrive: int
    off: int
    cycle: int


@dataclass
class Entry:
    start: datetime
    end: datetime
    status: str
    label: str
    stop: str | None
    loc: str | None
    a: tuple[float, float] | None
    b: tuple[float, float] | None
    miles: float
    empty: bool
    route_from: float | None
    route_to: float | None
    clocks: Clocks

    @property
    def minutes(self) -> int:
        return int(round(minutes_between(self.start, self.end)))


@dataclass
class Violation:
    code: str
    excess_minutes: int
    first_at: datetime
    limit: int
    detail: str = ""


@dataclass
class SimResult:
    start: datetime
    entries: list[Entry]
    marks: dict[str, datetime]
    violations: dict[str, Violation]
    breaks: list[Entry]
    resets: list[Entry]
    reserve: dict | None           # tightest reserve at any drive end
    final_reserve: dict | None     # reserve at the last drive end
    end: Clocks
    shifts: list[list[datetime]]
    cycle_limit: int

    def on_duty_minutes_after(self, t: datetime) -> int:
        total = 0.0
        for e in self.entries:
            if e.status in (ON, DRIVING) and e.end > t:
                total += minutes_between(max(e.start, t), e.end)
        return int(round(total))

    def drive_minutes(self) -> int:
        return sum(e.minutes for e in self.entries if e.status == DRIVING)

    def miles(self, empty: bool | None = None) -> float:
        return sum(e.miles for e in self.entries if e.status == DRIVING and (empty is None or e.empty == empty))

    @property
    def last_drive_end(self) -> datetime | None:
        ends = [e.end for e in self.entries if e.status == DRIVING]
        return max(ends) if ends else None


def _lerp(a, b, f):
    if a is None or b is None:
        return a or b
    return (a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1]))


class _Sim:
    def __init__(self, rules: Rules, cycle_limit: int, insert_breaks: bool):
        self.R = rules
        self.cycle_limit = cycle_limit
        self.insert_breaks = insert_breaks
        self.entries: list[Entry] = []
        self.marks: dict[str, datetime] = {}
        self.violations: dict[str, Violation] = {}
        self.breaks: list[Entry] = []
        self.resets: list[Entry] = []
        self.reserve: dict | None = None
        self.final_reserve: dict | None = None
        self.shifts: list[list[datetime]] = []

    # -- clock primitives --------------------------------------------------------
    def begin_duty(self, c: Clocks) -> None:
        if c.shift_start is None:
            c.shift_start = c.t
            self.shifts.append([c.t, c.t])
        elif not self.shifts:
            self.shifts.append([c.shift_start, c.t])
        c.off = 0

    def nondrive(self, c: Clocks, status: str, m: int) -> None:
        if m <= 0:
            return
        if status == ON:
            self.begin_duty(c)
            c.cycle += m
        else:
            c.off += m
        c.nondrive += m
        if c.nondrive >= self.R.break_len:
            c.since_break = 0
        if status == OFF and c.off >= self.R.reset:
            c.drive, c.since_break, c.shift_start = 0, 0, None
        if status == OFF and c.off >= self.R.restart:
            c.cycle = 0
        c.t += timedelta(minutes=m)
        c.status = status
        if status == ON and self.shifts:
            self.shifts[-1][1] = c.t

    def drive(self, c: Clocks, m: int) -> None:
        self.begin_duty(c)
        t0, R = c.t, self.R
        win_end = c.shift_start + timedelta(minutes=R.window)
        self._count("SHIFT_LIMIT", m, minutes_between(win_end, t0 + timedelta(minutes=m)), t0, R.window)
        self._count("DRIVE_LIMIT", m, c.drive + m - R.drive_limit, t0, R.drive_limit)
        self._count("CYCLE_LIMIT", m, c.cycle + m - self.cycle_limit, t0, self.cycle_limit)
        if not self.insert_breaks:
            self._count("BREAK_REQUIRED", m, c.since_break + m - R.break_after, t0, R.break_after)
        if R.company_drive_limit is not None and R.company_drive_limit < R.drive_limit:
            self._count("COMPANY_DRIVE_LIMIT", m, c.drive + m - R.company_drive_limit, t0, R.company_drive_limit)
        if R.company_window is not None and R.company_window < R.window:
            cw_end = c.shift_start + timedelta(minutes=R.company_window)
            self._count("COMPANY_WINDOW_LIMIT", m, minutes_between(cw_end, t0 + timedelta(minutes=m)), t0, R.company_window)
        c.drive += m
        c.since_break += m
        c.cycle += m
        c.nondrive = 0
        c.off = 0
        c.t = t0 + timedelta(minutes=m)
        c.status = DRIVING
        self.shifts[-1][1] = c.t
        self._track_reserve(c)

    def _count(self, code: str, m: int, past: float, t0: datetime, limit: int) -> None:
        over = int(round(max(0.0, min(float(m), past))))
        if over <= 0:
            return
        v = self.violations.get(code)
        if v is None:
            self.violations[code] = Violation(code, over, t0 + timedelta(minutes=m - over), limit)
        else:
            v.excess_minutes += over

    def _track_reserve(self, c: Clocks) -> None:
        R = self.R
        eff_drive = min(R.drive_limit, R.company_drive_limit or R.drive_limit)
        eff_window = min(R.window, R.company_window or R.window)
        drive_left = eff_drive - c.drive
        window_left = int(round(minutes_between(c.t, c.shift_start + timedelta(minutes=eff_window))))
        cycle_left = self.cycle_limit - c.cycle
        vals = {"drive_left": drive_left, "window_left": window_left, "cycle_left": cycle_left}
        binding = min(vals, key=vals.get)
        rec = {**vals, "reserve": vals[binding], "binding": binding, "at": c.t,
               "window_end": c.shift_start + timedelta(minutes=eff_window)}
        self.final_reserve = rec
        if self.reserve is None or rec["reserve"] < self.reserve["reserve"]:
            self.reserve = rec

    # -- entries -------------------------------------------------------------------
    def record(self, c_before: Clocks, c_after: Clocks, status: str, act: Act, label: str | None = None,
               a=None, b=None, miles: float = 0.0, rf=None, rt=None, stop: str | None = None) -> Entry:
        e = Entry(c_before.t, c_after.t, status, label or act.label, stop or act.stop, act.loc,
                  a if a is not None else act.a, b if b is not None else (act.b or act.a),
                  miles, act.empty, rf, rt, c_before)
        self.entries.append(e)
        return e


def initial_clocks(snap: EldSnapshot, rules: Rules) -> Clocks:
    t0 = utc(snap.observed_at)
    st = snap.duty_status.value
    st = OFF if st == "SLEEPER" else st
    since = max(0, int(minutes_between(utc(snap.status_since), t0)))
    c = Clocks(
        t=t0, status=st, shift_start=utc(snap.shift_start) if snap.shift_start else None,
        drive=snap.drive_minutes_in_shift, since_break=snap.drive_minutes_since_break,
        nondrive=since if st != DRIVING else 0, off=since if st == OFF else 0, cycle=snap.cycle_used_minutes,
    )
    if c.nondrive >= rules.break_len:
        c.since_break = 0
    if c.off >= rules.reset:
        c.drive, c.since_break, c.shift_start = 0, 0, None
    if c.off >= rules.restart:
        c.cycle = 0
    return c


def simulate(snap: EldSnapshot, acts: list[Act], rules: Rules, cycle_limit: int,
             start: datetime | None = None, insert_breaks: bool = True) -> SimResult:
    sim = _Sim(rules, cycle_limit, insert_breaks)
    c = initial_clocks(snap, rules)
    if c.shift_start is not None:
        sim.shifts.append([c.shift_start, c.t])
    sim_start = c.t
    if start is not None and utc(start) > c.t:
        # Carry the reported status forward to the plan start (no driving assumed).
        status = OFF if c.status == OFF else ON
        m = int(math.ceil(minutes_between(c.t, utc(start))))
        before = copy.copy(c)
        sim.nondrive(c, status, m)
        sim.record(before, copy.copy(c), status, Act("WAIT", label="Status per ELD", stop="status"))

    for act in acts:
        for mk in act.marks_start:
            sim.marks.setdefault(mk, c.t)
        if act.kind == "DRIVE":
            _run_drive(sim, c, act)
        else:
            if act.kind == "WAIT":
                m = int(math.ceil(minutes_between(c.t, utc(act.until)))) if act.until else 0
                status = act.wait_status
            else:
                m, status = act.minutes, (ON if act.kind == "ON" else OFF)
                if act.stop == "reset" and c.status == OFF:
                    m = max(0, m - c.off)  # credit a reset already in progress
            if m > 0:
                before = copy.copy(c)
                sim.nondrive(c, status, m)
                e = sim.record(before, copy.copy(c), status, act)
                if act.stop == "reset":
                    sim.resets.append(e)
        for mk in act.marks_end:
            sim.marks[mk] = c.t

    return SimResult(sim_start, sim.entries, sim.marks, sim.violations, sim.breaks, sim.resets,
                     sim.reserve, sim.final_reserve, c, sim.shifts, cycle_limit)


def _run_drive(sim: _Sim, c: Clocks, act: Act) -> None:
    R = sim.R
    total = act.minutes
    done = 0
    rf, rt = act.route_from, act.route_to
    while done < total:
        if sim.insert_breaks and c.since_break >= R.break_after:
            pos = _lerp(act.a, act.b, done / total)
            before = copy.copy(c)
            # Consecutive non-driving time already taken counts toward the break.
            sim.nondrive(c, OFF, max(1, R.break_len - c.nondrive))
            brk = Act("OFF", label="Required 30-min break", stop="break", loc=None, a=pos, b=pos)
            e = sim.record(before, copy.copy(c), OFF, brk)
            sim.breaks.append(e)
            continue
        remaining = total - done
        chunk = remaining if not sim.insert_breaks else min(remaining, R.break_after - c.since_break)
        f0, f1 = done / total, (done + chunk) / total
        before = copy.copy(c)
        sim.drive(c, chunk)
        r0 = rf + f0 * (rt - rf) if rf is not None else None
        r1 = rf + f1 * (rt - rf) if rf is not None else None
        sim.record(before, copy.copy(c), DRIVING, act, a=_lerp(act.a, act.b, f0), b=_lerp(act.a, act.b, f1),
                   miles=act.miles * (f1 - f0), rf=r0, rt=r1)
        done += chunk


def state_at(res: SimResult, t: datetime) -> tuple[Clocks, Entry | None, datetime | None]:
    """Projected clocks at time t, the entry in progress and when the current status began."""
    t = utc(t)
    if not res.entries or t < res.entries[0].start:
        return (copy.copy(res.entries[0].clocks) if res.entries else copy.copy(res.end)), None, None
    idx = None
    for i, e in enumerate(res.entries):
        if e.start <= t < e.end:
            idx = i
            break
    if idx is None:
        last = res.entries[-1]
        c = copy.copy(res.end)
        if t > c.t:  # idle after plan end: driver stays in last status (off duty after the work)
            m = int(minutes_between(c.t, t))
            dummy = _Sim(_rules_from_res(res), res.cycle_limit, True)
            dummy.nondrive(c, OFF if last.status == OFF else ON, m)
        return c, last, _status_since(res.entries, len(res.entries) - 1)
    e = res.entries[idx]
    c = copy.copy(e.clocks)
    m = int(minutes_between(e.start, t))
    dummy = _Sim(_rules_from_res(res), res.cycle_limit, True)
    if e.status == DRIVING:
        dummy.shifts.append([c.shift_start or c.t, c.t])
        dummy.drive(c, m)
    else:
        dummy.nondrive(c, e.status, m)
    c.status = e.status  # also correct at the very first minute of an entry
    return c, e, _status_since(res.entries, idx)


def _rules_from_res(res: SimResult) -> Rules:
    from ..config import get_rules
    return get_rules()


def _status_since(entries: list[Entry], idx: int) -> datetime:
    st = entries[idx].status
    j = idx
    while j > 0 and entries[j - 1].status == st:
        j -= 1
    return entries[j].start
