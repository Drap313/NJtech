"""Milestone 4: three ranked alternatives, illegal plans excluded, every plan rechecked."""
from datetime import datetime

from app.optimization.solver import find_costed_alternatives
from tests.conftest import at


def alts(fleet):
    fleet.assignments["A-204"].pickup_delay_minutes = 75
    return find_costed_alternatives(fleet, "A-204", at("06:40"))


def test_three_ranked_alternatives_match_expected_incident(fleet):
    r = alts(fleet)
    plans = r["plans"]
    assert [p["kind"] for p in plans] == ["RELAY", "SWAP_DRIVER", "HOLD_FOR_RESET"]
    relay, swap, hold = plans
    assert relay["title"].startswith("Relay to D-03 at Exit 18")
    assert datetime.fromisoformat(relay["delivery_eta"]) == at("18:38")
    assert relay["appointment_slack_minutes"] == 22
    assert relay["hos_reserve_minutes"] == 64
    assert round(relay["incremental_cost"]) == 286
    assert swap["title"].startswith("Swap to D-09") and swap["incremental_cost"] == 411.0
    assert hold["incremental_cost"] == 1200.0 and hold["service_tier"] == "LATE"


def test_every_presented_plan_passes_and_is_independently_rechecked(fleet):
    for p in alts(fleet)["plans"]:
        assert p["compliance_verdict"] == "PASS"
        assert p["recheck"]["verdict"] == "PASS" and p["recheck"]["match"]
        for k in ("delivery_eta", "incremental_cost", "hos_reserve_minutes", "assumptions", "expires_at", "cost_breakdown"):
            assert p[k] is not None


def test_illegal_plans_are_excluded(fleet):
    r = alts(fleet)
    assert r["current_plan"]["compliance_verdict"] == "FAIL"
    rejected = {x["title"]: x["codes"] for x in r["rejected"]}
    assert "CYCLE_LIMIT" in rejected["Relay to D-06 at Harrisburg Relay Lot (I-81, PA)"]
    titles = [p["title"] for p in r["plans"]]
    assert not any("D-06" in t or "D-10" in t for t in titles)


def test_cost_breakdown_separates_contract_from_estimate(fleet):
    swap = alts(fleet)["plans"][1]
    bases = {l["component"]: l["basis"] for l in swap["cost_breakdown"]}
    assert bases["Driver call in premium"] == "contract"
    assert bases["Driver time"] == "estimate"
    assert round(sum(l["amount"] for l in swap["cost_breakdown"]), 2) == swap["incremental_cost"]


def test_recommendation_expires_with_its_freshest_input(fleet):
    relay = alts(fleet)["plans"][0]
    assert datetime.fromisoformat(relay["expires_at"]) == at("06:52")  # D-03 ELD (06:22) + 30 min
    assert "D-03" in relay["expiry_reason"]
