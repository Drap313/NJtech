"""Slack bridge without a real workspace: block rendering, outbox flush, approval path."""
from types import SimpleNamespace

from app.agent.tools import ToolBox
from app.events.processor import make_event
from app.incidents import service
from app.integrations.slack_bridge import SlackBridge, incident_blocks, option_blocks
from tests.conftest import at


class FakeClient:
    def __init__(self):
        self.posts, self.updates = [], []

    def chat_postMessage(self, **kw):
        self.posts.append(kw)
        return {"ts": f"{len(self.posts)}.000"}

    def chat_update(self, **kw):
        self.updates.append(kw)
        return {"ok": True}


def bridge(env):
    store, clock, engine = env
    ctx = SimpleNamespace(store=store, clock=clock, engine=engine, tools=ToolBox(store, clock), llm=None)
    b = SlackBridge(ctx)
    b.app = SimpleNamespace(client=FakeClient())
    b.channel_id = "C0DISPATCH"
    return b


def delay(engine):
    engine.process_event(make_event("PICKUP_DELAYED", "load", "DG-204", {"delay_minutes": 75}, "test", at("06:40")))


def test_incident_card_has_recommendation_and_gated_buttons(env):
    store, _, engine = env
    delay(engine)
    inc = store.get_incident("DG-I-0042")
    blocks = incident_blocks(inc, "http://localhost:8090")
    text = str(blocks)
    assert "Relay to D-03" in text and "$286" in text
    actions = next(b for b in blocks if b["type"] == "actions")["elements"]
    approve = next(a for a in actions if a["action_id"] == "dg_approve")
    assert approve["value"] == inc["recommended_plan_id"] and "confirm" in approve
    assert all(len(b.get("text", {}).get("text", "")) < 3000 for b in blocks)
    opts = option_blocks(inc)
    assert sum(1 for b in opts if b.get("accessory", {}).get("action_id", "").startswith("dg_approve")) == 3


def test_flush_posts_each_incident_once_then_updates_in_place(env):
    store, _, engine = env
    b = bridge(env)
    assert b.flush() == 1  # DG-I-0041 (stale feed) from the seed sweep
    delay(engine)
    assert b.flush() == 1  # DG-I-0042
    assert b.flush() == 0  # nothing new
    assert [p["text"].split(" ")[0] for p in b.app.client.posts] == ["DG-I-0041", "DG-I-0042"]
    assert all(r["status"] == "sent" for r in store.outbox_list(50) if r["channel"] == "dispatcher")
    # An incident revision updates the original card instead of posting a new one.
    inc = store.get_incident("DG-I-0042")
    b.post_incident(inc)
    assert b.app.client.updates and b.app.client.updates[-1]["ts"] == "2.000"


def test_slack_approval_goes_through_the_same_gate(env):
    store, clock, engine = env
    delay(engine)
    pid = store.get_incident("DG-I-0042")["recommended_plan_id"]
    tok = service.approve_plan(store, clock, pid, "slack:U123:alex")
    res = service.execute_approved_plan(store, clock, pid, tok["approval_token"], f"slack:{pid}")
    assert res["approved_by"] == "slack:U123:alex"
    again = service.execute_approved_plan(store, clock, pid, tok["approval_token"], f"slack:{pid}")
    assert again.get("duplicate")  # double-clicks in Slack are idempotent


def test_bridge_disabled_without_tokens(env, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    b = SlackBridge(SimpleNamespace(store=env[0], clock=env[1]))
    b.start()
    assert b.status["enabled"] is False and "disabled" in b.status["last_error"]
