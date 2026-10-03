"""Boundary tests required by the build spec (section 5)."""
import ast
from datetime import datetime
from pathlib import Path

import pytest

from app.compliance.engine import check_plan
from app.compliance.hos import Act, simulate
from app.config import get_rules
from app.forecasting.evaluator import evaluate_assignment
from app.forecasting.planner import Leg, PlanBuild
from app.models import EldSnapshot
from tests.conftest import at, plus

R = get_rules()
CYCLE = R.cycle_limit("70_8")


def fresh_start(t, cycle=0):
    """Driver just finished a 10-hour rest at time t."""
    return EldSnapshot(observed_at=t, duty_status="OFF_DUTY", status_since=plus(t, -700), shift_start=None, cycle_used_minutes=cycle)


def drive(minutes):
    return Act("DRIVE", minutes, label="drive")


def run(acts, snap=None, rules=R, cycle=CYCLE, breaks=True):
    return simulate(snap or fresh_start(at("06:00")), acts, rules, cycle, insert_breaks=breaks)


# ---- 11-hour driving limit ------------------------------------------------------
def test_exactly_at_driving_limit_passes():
    res = run([drive(400), Act("ON", 30), drive(260)])  # 660 driving, break satisfied by the on-duty stop
    assert "DRIVE_LIMIT" not in res.violations
    assert res.reserve["drive_left"] == 0


def test_one_minute_beyond_driving_limit_fails():
    res = run([drive(400), Act("ON", 30), drive(261)])
    v = res.violations["DRIVE_LIMIT"]
    assert v.excess_minutes == 1
    assert v.first_at == plus(at("06:00"), 400 + 30 + 260)


# ---- 14-hour window ---------------------------------------------------------------
def test_exactly_at_window_end_passes():
    res = run([Act("ON", 300), drive(240), Act("OFF", 60), drive(240)])  # last drive ends at hour 14 exactly
    assert "SHIFT_LIMIT" not in res.violations
    assert res.reserve["window_left"] == 0


def test_one_minute_past_window_fails():
    res = run([Act("ON", 301), drive(240), Act("OFF", 60), drive(240)])
    assert res.violations["SHIFT_LIMIT"].excess_minutes == 1


def test_on_duty_work_after_window_is_legal():
    res = run([Act("ON", 600), drive(200), Act("ON", 120)])  # unloading past hour 14 is not driving
    assert "SHIFT_LIMIT" not in res.violations


# ---- 30-minute break ----------------------------------------------------------------
def test_break_becomes_due_during_a_planned_leg():
    res = run([drive(600)])
    assert len(res.breaks) == 1
    b = res.breaks[0]
    assert b.start == plus(at("06:00"), 480) and b.minutes == 30
    assert res.end.t == plus(at("06:00"), 630)  # ETA includes the break


def test_break_not_inserted_flags_violation():
    res = run([drive(500)], breaks=False)
    assert res.violations["BREAK_REQUIRED"].excess_minutes == 20


def test_on_duty_stop_satisfies_break():
    res = run([drive(470), Act("ON", 30), drive(100)])
    assert res.breaks == []


# ---- cycle, reset, restart ----------------------------------------------------------------
def test_cycle_exactly_at_limit_and_one_beyond():
    ok = run([drive(120)], snap=fresh_start(at("06:00"), cycle=CYCLE - 120))
    assert "CYCLE_LIMIT" not in ok.violations
    bad = run([drive(121)], snap=fresh_start(at("06:00"), cycle=CYCLE - 120))
    assert bad.violations["CYCLE_LIMIT"].excess_minutes == 1


def test_ten_hour_reset_restores_clocks():
    res = run([drive(600), Act("OFF", 600, stop="reset"), drive(300)])
    assert not res.violations
    assert len(res.shifts) == 2


def test_nine_hour_fifty_nine_rest_does_not_reset():
    res = run([drive(600), Act("OFF", 599), drive(100)])
    assert "SHIFT_LIMIT" in res.violations or "DRIVE_LIMIT" in res.violations


def test_34_hour_restart_resets_cycle():
    snap = EldSnapshot(observed_at=at("06:00"), duty_status="OFF_DUTY", status_since=plus(at("06:00"), -2040),
                       cycle_used_minutes=CYCLE)
    res = run([drive(60)], snap=snap)
    assert "CYCLE_LIMIT" not in res.violations


# ---- company policy stricter than law --------------------------------------------------------
def test_company_policy_stricter_than_legal_baseline():
    strict = R.with_overrides(company_drive_limit=600)
    res = run([drive(400), Act("ON", 30), drive(230)], rules=strict)  # 630 min: legal, not company-legal
    assert "DRIVE_LIMIT" not in res.violations
    assert res.violations["COMPANY_DRIVE_LIMIT"].excess_minutes == 30


# ---- qualifications, stale data, verdict assembly ----------------------------------------------
def test_missing_endorsement_fails(fleet):
    ev = evaluate_assignment(fleet, "A-207", at("06:40"))  # hazmat tanker proposed to D-06 (no H/N)
    codes = [h["code"] for h in ev["compliance"]["hard_failures"]]
    assert ev["verdict"] == "FAIL"
    assert codes.count("ENDORSEMENT_MISSING") == 2
    assert all(h["citation"].startswith("49 CFR 383.93") for h in ev["compliance"]["hard_failures"] if h["code"] == "ENDORSEMENT_MISSING")


def test_stale_eld_is_unknown_never_pass(fleet):
    ev = evaluate_assignment(fleet, "A-205", at("06:40"))  # D-05 last reported 03:10
    assert ev["verdict"] == "UNKNOWN"
    assert ev["compliance"]["unknowns"][0]["code"] == "STALE_ELD"


def test_planned_equipment_outage_blocks_overlapping_plan(fleet):
    t6 = fleet.tractors["T-06"]
    a = fleet.assignments["A-208"]
    a.tractor_id = "T-06"
    load = fleet.loads[a.load_id]
    load.pickup_window.start = datetime.fromisoformat("2026-10-08T09:00:00-04:00")
    load.pickup_window.end = datetime.fromisoformat("2026-10-08T10:00:00-04:00")
    load.delivery_window.start = datetime.fromisoformat("2026-10-08T12:00:00-04:00")
    load.delivery_window.end = datetime.fromisoformat("2026-10-08T15:00:00-04:00")
    ev = evaluate_assignment(fleet, "A-208", at("06:40"))
    assert any(h["code"] == "EQUIPMENT_UNAVAILABLE" and h["equipment_id"] == t6.id for h in ev["compliance"]["hard_failures"])


def test_time_zone_transition_crosses_appointment_boundary(fleet):
    """Chicago (CDT) pickup, Columbus (EDT) delivery window. Comparing local wall
    clocks would be off by an hour; everything must be compared in absolute time."""
    ev = evaluate_assignment(fleet, "A-209", at("06:40"))
    assert ev["verdict"] == "PASS"
    eta = datetime.fromisoformat(ev["delivery_eta"])
    # Loading ends 09:00 CDT == 10:00 EDT; 330 min transit -> 15:30 EDT; window ends 17:00 EDT.
    assert eta == datetime.fromisoformat("2026-10-07T15:30:00-04:00")
    assert ev["appointment_slack_minutes"] == 90
    # Push the delivery window one hour earlier in EDT: a naive local comparison would still look on time.
    load = fleet.loads["A-209".replace("A-", "DG-")]
    load.delivery_window.end = datetime.fromisoformat("2026-10-07T15:00:00-04:00")
    load.delivery_window.start = datetime.fromisoformat("2026-10-07T13:00:00-04:00")
    late = evaluate_assignment(fleet, "A-209", at("06:40"))
    assert late["appointment_slack_minutes"] == -30
    assert late["feasibility"]["service_tier"] == "LATE"


def test_cycle_overrun_across_midnight_goes_to_manual_review(fleet):
    d = fleet.drivers["D-09"]
    d.eld.cycle_used_minutes = R.cycle_limit("70_8") - 60
    leg = Leg(d, "primary", [Act("WAIT", until=at("23:00"), wait_status="OFF_DUTY"), drive(90)], None, d.eld.observed_at, None, False)
    leg.sim = simulate(d.eld, leg.acts, R, R.cycle_limit("70_8"))
    pb = PlanBuild(kind="CONTINUE", actions=[], title="t", legs=[leg])
    pb.marks["delivered"] = leg.sim.end.t
    res = check_plan(fleet, pb, fleet.loads["DG-204"], at("06:40"))
    assert res["verdict"] == "MANUAL_REVIEW"
    assert res["manual_review"][0]["code"] == "CYCLE_ROLLOFF_NOT_MODELED"


def test_every_verdict_has_versioned_rule_trace(fleet):
    ev = evaluate_assignment(fleet, "A-204", at("06:40"))
    c = ev["compliance"]
    assert c["ruleset"] == "us_federal_property_v1@1.0.0" and c["company_policy"].startswith("company_policy_v1@")
    rules = {t["rule"] for t in c["rule_trace"]}
    assert {"DRIVE_LIMIT", "SHIFT_LIMIT", "CYCLE_LIMIT", "CDL_CLASS", "DATA_FRESHNESS"} <= rules


def test_compliance_code_never_imports_llm():
    """No language model participates in a verdict."""
    root = Path(__file__).resolve().parents[2] / "app" / "compliance"
    for f in root.glob("*.py"):
        tree = ast.parse(f.read_text())
        mods = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        mods += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any("agent" in m or "httpx" in m or "openai" in m for m in mods), f.name
