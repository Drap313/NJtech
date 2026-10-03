"""Milestone 6 exit condition: the model can explain and recommend but cannot
mutate state without a dispatcher's approval."""
import pytest

from app.agent.tools import ToolBox
from app.events.processor import make_event
from app.incidents import service
from app.state import Fleet
from tests.conftest import at


@pytest.fixture
def incident(env):
    store, clock, engine = env
    engine.process_event(make_event("PICKUP_DELAYED", "load", "DG-204", {"delay_minutes": 75}, "test", at("06:40")))
    return store.get_incident("DG-I-0042")


def snapshot(store):
    a = Fleet.load(store).assignments["A-204"]
    return (a.driver_id, a.relay, a.revision)


def test_mutating_tools_declare_gates(env):
    tb = ToolBox(env[0], env[1])
    gated = {n for n, t in tb.tools.items() if t.needs_approval}
    assert gated == {"execute_approved_plan", "record_override"}
    assert all(t.mutating for t in tb.tools.values() if t.needs_approval)


def test_agent_cannot_execute_without_token(env, incident):
    store, clock, _ = env
    tb = ToolBox(store, clock)
    before = snapshot(store)
    out = tb.call("execute_approved_plan", {"plan_id": incident["recommended_plan_id"], "idempotency_key": "x"})
    assert out["error"] == "APPROVAL_REQUIRED"
    out = tb.call("execute_approved_plan", {"plan_id": incident["recommended_plan_id"], "approval_token": "apv_forged", "idempotency_key": "y"})
    assert out["error"] == "APPROVAL_REQUIRED"
    assert snapshot(store) == before
    assert store.get_plan(incident["recommended_plan_id"])[1] == "PROPOSED"
    assert any(r["kind"] == "agent_blocked" for r in store.audit_for("tool:execute_approved_plan"))


def test_mutating_tool_requires_idempotency_key(env, incident):
    tb = ToolBox(env[0], env[1])
    out = tb.call("request_driver_confirmation", {"driver_id": "D-01", "question": "status?"})
    assert out["error"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_agent_identity_cannot_approve(env, incident):
    store, clock, _ = env
    with pytest.raises(service.ApprovalError):
        service.approve_plan(store, clock, incident["recommended_plan_id"], "agent:planner")


def test_approved_execution_is_single_use_idempotent_and_audited(env, incident):
    store, clock, _ = env
    pid = incident["recommended_plan_id"]
    tok = service.approve_plan(store, clock, pid, "dispatcher:alex")
    tb = ToolBox(store, clock)
    out = tb.call("execute_approved_plan", {"plan_id": pid, "approval_token": tok["approval_token"], "idempotency_key": "run-1"})
    assert out["revalidation"]["verdict"] == "PASS"
    assert snapshot(store)[1].driver_id == "D-03"
    again = tb.call("execute_approved_plan", {"plan_id": pid, "approval_token": tok["approval_token"], "idempotency_key": "run-1"})
    assert again.get("duplicate") is True
    reuse = tb.call("execute_approved_plan", {"plan_id": pid, "approval_token": tok["approval_token"], "idempotency_key": "run-2"})
    assert reuse["error"] in ("APPROVAL_REQUIRED", "REJECTED")
    kinds = [r["kind"] for r in store.audit_for("incident:DG-I-0042")]
    assert "plan_approved" in kinds and "execution" in kinds
    assert store.get_incident("DG-I-0042")["status"] == "EXECUTED"


def test_expired_plan_cannot_be_approved(env, incident):
    store, clock, _ = env
    clock.set(at("07:30"), paused=True)
    with pytest.raises(service.ApprovalError, match="expired"):
        service.approve_plan(store, clock, incident["recommended_plan_id"], "dispatcher:alex")


def test_hard_failure_cannot_be_overridden(env, incident):
    store, clock, _ = env
    with pytest.raises(service.ApprovalError, match="PROHIBITED"):
        service.record_override(store, clock, "DG-I-0042", "dispatcher:alex", "customer insists", "ov-1")


def test_execution_revalidates_against_current_state(env, incident):
    store, clock, engine = env
    pid = incident["recommended_plan_id"]
    tok = service.approve_plan(store, clock, pid, "dispatcher:alex")
    # Between approval and execution, D-03's tractor breaks down.
    engine.process_event(make_event("VEHICLE_UNAVAILABLE", "tractor", "T-03", {"reason": "flat"}, "test", at("06:41")))
    with pytest.raises(service.ApprovalError, match="revalidation failed"):
        service.execute_approved_plan(store, clock, pid, tok["approval_token"], "run-x")
    assert snapshot(store)[1] is None
