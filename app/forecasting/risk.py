"""Schedule-risk policy: transparent points with evidence. Not fatigue detection."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..clock import minutes_between, utc
from ..compliance.hos import SimResult
from ..config import get_risk_policy
from ..models import Driver
from ..routing import Network


def _overlaps_night(a: datetime, b: datetime, tz: ZoneInfo, n0: time, n1: time) -> bool:
    la, lb = a.astimezone(tz), b.astimezone(tz)
    d = la.date() - timedelta(days=1)
    while d <= lb.date():
        s = datetime.combine(d, n0, tz)
        e = datetime.combine(d, n1, tz)
        if la < e and lb > s:
            return True
        d += timedelta(days=1)
    return False


def score_schedule_risk(driver: Driver, sim: SimResult | None, now: datetime, net: Network, changed: bool = False) -> dict:
    pol = get_risk_policy()
    F = pol["factors"]
    now = utc(now)
    tz = ZoneInfo(net.loc(driver.home_terminal).tz)
    factors: list[dict] = []

    shifts = [(utc(s.start), utc(s.end)) for s in driver.recent_shifts]
    planned = [(s[0], s[1]) for s in (sim.shifts if sim else [])]
    for p in planned:
        if all(abs(minutes_between(p[0], s[0])) > 1 for s in shifts):
            shifts.append(p)
    shifts.sort()

    f = F["REPEATED_OVERNIGHT"]
    n0, n1 = time.fromisoformat(f["night_start"]), time.fromisoformat(f["night_end"])
    since = now - timedelta(hours=f["lookback_hours"])
    nights = [s for s in shifts if s[1] >= since and _overlaps_night(s[0], s[1], tz, n0, n1)]
    if len(nights) >= f["min_count"]:
        factors.append({"code": "REPEATED_OVERNIGHT", "points": f["points"],
                        "evidence": f"{len(nights)} duty periods touching 00:00-05:00 in the last {f['lookback_hours']} h"})

    f = F["SHORT_TURNAROUND"]
    for prev, nxt in zip(shifts, shifts[1:]):
        if nxt in planned or nxt[1] >= now:
            gap = minutes_between(prev[1], nxt[0])
            if 0 <= gap < f["threshold_minutes"]:
                factors.append({"code": "SHORT_TURNAROUND", "points": f["points"],
                                "evidence": f"{gap / 60:.1f} h off between duty periods (threshold {f['threshold_minutes'] / 60:.0f} h)"})
                break

    res = sim.reserve if sim else None
    if res is not None:
        f = F["LOW_PROJECTED_RESERVE"]
        if res["reserve"] < f["threshold_minutes"]:
            factors.append({"code": "LOW_PROJECTED_RESERVE", "points": f["points"],
                            "evidence": f"projected HOS reserve {res['reserve']} min"})
        f = F["DRIVING_NEAR_WINDOW_END"]
        fin = sim.final_reserve
        if fin and fin["window_left"] < f["threshold_minutes"]:
            factors.append({"code": "DRIVING_NEAR_WINDOW_END", "points": f["points"],
                            "evidence": f"last driving ends {fin['window_left']} min before the duty window closes"})

    f = F["RECENT_SCHEDULE_CHANGE"]
    recent = driver.last_schedule_change_at and minutes_between(utc(driver.last_schedule_change_at), now) <= f["lookback_hours"] * 60
    if changed or recent:
        factors.append({"code": "RECENT_SCHEDULE_CHANGE", "points": f["points"],
                        "evidence": "plan changes this driver's schedule" if changed else "schedule changed in the last 24 h"})

    f = F["FRESH_CONFIRMATION_AMPLE_RESERVE"]
    if driver.last_confirmation_at and res is not None:
        age = minutes_between(utc(driver.last_confirmation_at), now)
        if 0 <= age <= f["confirmation_minutes"] and res["reserve"] >= f["reserve_minutes"]:
            factors.append({"code": "FRESH_CONFIRMATION_AMPLE_RESERVE", "points": f["points"],
                            "evidence": f"driver confirmed {age:.0f} min ago; reserve {res['reserve']} min"})

    score = max(0, min(100, sum(x["points"] for x in factors)))
    band = "normal"
    for b in pol["bands"]:
        if score >= b["min"]:
            band = b["name"]
    return {"driver_id": driver.id, "score": score, "band": band, "factors": factors,
            "policy": f"{pol['id']}@{pol['version']}", "disclaimer": pol["disclaimer"]}
