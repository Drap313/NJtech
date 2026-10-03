"""Milestone 3: a delay predictably changes ETA, feasibility and cost."""
from datetime import datetime

from app.forecasting.evaluator import evaluate_assignment
from tests.conftest import at


def test_baseline_dg204_is_legal_with_thin_reserve(fleet):
    ev = evaluate_assignment(fleet, "A-204", at("06:40"))
    assert ev["verdict"] == "PASS"
    assert datetime.fromisoformat(ev["delivery_eta"]) == at("17:38")
    assert ev["appointment_slack_minutes"] == 82
    assert ev["hos_reserve_minutes"] == 34
    assert [s["type"] for s in ev["required_stops"]].count("break") == 1


def test_pickup_delay_shifts_eta_and_breaks_duty_window(fleet):
    fleet.assignments["A-204"].pickup_delay_minutes = 75
    ev = evaluate_assignment(fleet, "A-204", at("06:40"))
    assert datetime.fromisoformat(ev["delivery_eta"]) == at("18:53")  # exactly +75
    assert ev["verdict"] == "FAIL"
    (fail,) = ev["compliance"]["hard_failures"]
    assert fail["code"] == "SHIFT_LIMIT" and fail["excess_minutes"] == 41
    assert ev["feasibility"]["first_failure"]["code"] == "SHIFT_LIMIT"
    assert datetime.fromisoformat(ev["feasibility"]["first_failure"]["at"]) == at("18:12")


def test_delay_changes_cost_and_risk(fleet):
    before = evaluate_assignment(fleet, "A-204", at("06:40"))
    fleet.assignments["A-204"].pickup_delay_minutes = 75
    after = evaluate_assignment(fleet, "A-204", at("06:40"))
    assert after["cost_components"]["labor_minutes"] - before["cost_components"]["labor_minutes"] == 75
    assert after["risk"]["score"] > before["risk"]["score"]
    codes = {f["code"] for f in after["risk"]["factors"]}
    assert "LOW_PROJECTED_RESERVE" in codes
    assert "fatigue" not in after["risk"]["disclaimer"].lower().replace("not a fatigue", "")


def test_delay_consuming_reserve_trips_company_minimum(fleet):
    fleet.assignments["A-204"].pickup_delay_minutes = 34  # consumes the reserve exactly
    ev = evaluate_assignment(fleet, "A-204", at("06:40"))
    assert ev["hos_reserve_minutes"] == 0
    assert ev["verdict"] == "FAIL"  # company minimum reserve (15 min) is stricter than the law
    assert [h["code"] for h in ev["compliance"]["hard_failures"]] == ["COMPANY_MIN_RESERVE"]
