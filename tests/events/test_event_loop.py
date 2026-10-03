"""Milestone 5: a pickup delay automatically opens one incident; dedupe; mock fleet."""
from datetime import timedelta

from app.events.processor import make_event
from app.events.runtime import FleetRuntime
from app.incidents import service
from app.state import Fleet
from tests.conftest import at


def delay(engine, key="k1"):
    return engine.process_event(make_event("PICKUP_DELAYED", "load", "DG-204", {"delay_minutes": 75}, "test", at("06:40"),
                                           idempotency_key=key))


def test_seed_sweep_flags_only_the_stale_feed(env):
    store, clock, engine = env
    open_ = store.list_incidents("OPEN")
    assert [i["id"] for i in open_] == ["DG-I-0041"]
    assert open_[0]["assignment_id"] == "A-205" and open_[0]["evaluation"]["verdict"] == "UNKNOWN"
    assert any(o["recipient"] == "D-05" and o["channel"] == "driver" for o in store.outbox_list())
    assert Fleet.load(store).assignments["A-207"].status == "BLOCKED"  # pre-dispatch gate


def test_pickup_delay_opens_exactly_one_incident_fast(env):
    store, clock, engine = env
    res = delay(engine)
    assert res["processing_ms"] < 5000
    (a,) = res["affected"]
    assert a["verdict"] == "FAIL" and a["incident"] == {"action": "opened", "incident_id": "DG-I-0042"}
    inc = store.get_incident("DG-I-0042")
    assert inc["severity"] == "HIGH" and len(inc["plans"]) == 3
    assert "41 minutes" in inc["cause"]


def test_duplicate_event_is_idempotent(env):
    store, clock, engine = env
    delay(engine, "same")
    again = delay(engine, "same")
    assert again["status"] == "duplicate"
    assert Fleet.load(store).assignments["A-204"].pickup_delay_minutes == 75


def test_repeated_reevaluation_is_suppressed(env):
    store, clock, engine = env
    delay(engine)
    engine.sweep()
    engine.sweep()
    inc = store.get_incident("DG-I-0042")
    assert inc["revision"] == 1 and inc["suppressed_count"] >= 2
    assert len([o for o in store.outbox_list(100) if o["incident_id"] == "DG-I-0042" and o["channel"] == "dispatcher"]) == 1


def test_mock_fleet_completes_relay_on_time(env):
    store, clock, engine = env
    delay(engine)
    pid = store.get_incident("DG-I-0042")["recommended_plan_id"]
    tok = service.approve_plan(store, clock, pid, "dispatcher:test")
    service.execute_approved_plan(store, clock, pid, tok["approval_token"], "exec-1")
    rt = FleetRuntime(engine)
    now = at("06:40")
    while now < at("20:00"):
        now += timedelta(minutes=5)
        clock.set(now, paused=False)
        rt.tick()
        clock.set(now, paused=True)
        assert rt.last_error is None
        ev = store.get_evaluation("A-204")
        if Fleet.load(store).assignments["A-204"].status == "COMPLETED":
            break
        assert ev["verdict"] == "PASS", (now, ev["compliance"]["hard_failures"])
        assert ev["appointment_slack_minutes"] >= 20  # no ETA drift while the plan runs
    a = Fleet.load(store).assignments["A-204"]
    assert a.status == "COMPLETED" and a.driver_id == "D-03"
    hist = " ".join(h["what"] for h in a.history if "what" in h)
    assert "relay completed" in hist
    delivered = [e for e in store.all_events() if e["type"] == "ARRIVED_AT_DELIVERY" and e["entity_id"] == "A-204"]
    assert delivered and delivered[0]["occurred_at"].startswith("2026-10-06T22:38")
