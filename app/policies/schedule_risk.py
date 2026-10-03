"""
Schedule-based risk policy.

This is NOT a medical fatigue diagnosis. It is a transparent, explainable
score over observable scheduling patterns -- the kind of thing a careful
safety manager would flag by eye. Every output names the specific factors
that drove it, in plain language, so a dispatcher can judge it rather than
trust it blindly.
"""
from __future__ import annotations

from datetime import datetime, timedelta

CIRCADIAN_LOW_START_HOUR = 0   # 00:00
CIRCADIAN_LOW_END_HOUR = 6     # 06:00


def _circadian_low_minutes(start: datetime, end: datetime) -> float:
    """Minutes of [start, end] that fall inside any 00:00-06:00 local window."""
    if end <= start:
        return 0.0
    total = 0.0
    cursor = start
    while cursor < end:
        day_low_start = cursor.replace(hour=CIRCADIAN_LOW_START_HOUR, minute=0, second=0, microsecond=0)
        day_low_end = cursor.replace(hour=CIRCADIAN_LOW_END_HOUR, minute=0, second=0, microsecond=0)
        if day_low_end <= cursor:
            day_low_start += timedelta(days=1)
            day_low_end += timedelta(days=1)
        overlap_start = max(cursor, day_low_start)
        overlap_end = min(end, day_low_end)
        if overlap_end > overlap_start:
            total += (overlap_end - overlap_start).total_seconds() / 60
        cursor = day_low_end if day_low_end > cursor else cursor + timedelta(days=1)
    return total


def score_schedule_risk(
    driver,
    planned_start: datetime,
    planned_end: datetime,
    planned_reserve_minutes: int,
) -> dict:
    """
    score_schedule_risk(driver, load) -> risk state + explanation.

    Signature here is expanded (planned_start/end/reserve) so it's directly
    testable; app/api wraps it after calling evaluate_assignment.
    """
    points = 0
    factors: list[str] = []

    if driver.reported_status == "needs_rest":
        points += 100  # hard floor: see Agent safety tests. This alone forces "high".
        factors.append(f"{driver.name} has reported needing rest")

    if driver.consecutive_overnight_assignments >= 3:
        points += 3
        factors.append(f"{driver.name.split()[0] if ' ' in driver.name else driver.name}'s "
                        f"{_ordinal(driver.consecutive_overnight_assignments + 1)} "
                        f"consecutive overnight assignment")
    elif driver.consecutive_overnight_assignments == 2:
        points += 2
        factors.append("third consecutive overnight assignment")
    elif driver.consecutive_overnight_assignments == 1:
        points += 1
        factors.append("second consecutive overnight assignment")

    circadian_minutes = _circadian_low_minutes(planned_start, planned_end)
    if circadian_minutes > 0:
        hrs = circadian_minutes / 60
        pts = 2 if hrs >= 3 else 1
        points += pts
        factors.append(f"{hrs:.1f} planned driving hours during the circadian-low window")

    if driver.hours_since_last_reset < 9:
        points += 2
        factors.append(f"only {driver.hours_since_last_reset:.1f}h off before this shift")
    elif driver.hours_since_last_reset < 10.5:
        points += 1
        factors.append(f"only {driver.hours_since_last_reset:.1f}h off before this shift")

    if planned_reserve_minutes < 45:
        points += 2
        factors.append(f"only {planned_reserve_minutes} minutes of HOS reserve")
    elif planned_reserve_minutes < 75:
        points += 1
        factors.append(f"a tight {planned_reserve_minutes}-minute HOS reserve")

    if driver.cycle_minutes_remaining <= 180:
        points += 1
        factors.append("close to the duty-cycle limit")

    level = "high" if points >= 100 else "high" if points >= 5 else "moderate" if points >= 2 else "low"

    if not factors:
        explanation = f"No elevated schedule-risk factors for {driver.name}."
    else:
        explanation = "Elevated schedule risk: " + ", ".join(factors) + "." if level != "low" \
            else "Minor schedule factors noted: " + ", ".join(factors) + "."

    return {
        "schedule_risk_state": level,
        "score": points,
        "factors": factors,
        "explanation": explanation,
    }


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
