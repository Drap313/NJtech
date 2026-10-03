"""Slack alerts bridge without a real workspace: cards, post-once/edit-in-place, button handlers, approval gate."""
from types import SimpleNamespace

from app.agent.tools import ToolBox
from app.events.processor import make_event
from app.incidents import service
from app.integrations.slack_bridge import SlackBridge, approve_from_slack, incident_blocks, option_blocks, plain_cause
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


class FakeApp:
    """Captures Bolt registrations so handlers can be invoked like Slack would."""
    def __init__(self):
        self.client = FakeClient()
        self.actions, self.events = [], {}

    def action(self, matcher):
        def deco(fn):
            self.actions.append((matcher, fn))
            return fn
        return deco

    def event(self, name):
        def deco(fn):
            self.events[name] = fn
            return fn
        return deco

    def click(self, action_id, value, user="U0C628NKALF", message_ts="2.000"):
        fn = next(f for m, f in self.actions if (m.match(action_id) if hasattr(m, "match") else m == action_id))
        acked = []
        body = {"user": {"id": user, "username": "alex"}, "channel": {"id": "C0DISPATCH"}, "message": {"ts": message_ts}}
        fn(ack=lambda: acked.append(1), body=body, action={"action_id": action_id, "value": value})
        assert acked, "handler must ack within 3 s"


def bridge(env, approvers=""):
    store, clock, engine = env
    ctx = SimpleNamespace(store=store, clock=clock, engine=engine, tools=ToolBox(store, clock), llm=None)
    b = SlackBridge(ctx)
    b.approvers = {u for u in approvers.split(",") if u}
    b.app = FakeApp()
    b._register(b.app)
    b.channel_id = "C0DISPATCH"
    return b


def delay(engine):
    engine.process_event(make_event("PICKUP_DELAYED", "load", "DG-204", {"delay_minutes": 75}, "test", at("06:40")))


def test_incident_card_is_plain_language_with_gated_buttons(env):
    store, _, engine = env
    delay(engine)
    inc = store.get_incident("DG-I-0042")
    blocks = incident_blocks(inc, "http://localhost:8090")
    text = str(blocks)
    assert plain_cause(inc).startswith("D-01 would drive 41 min past the 14-hour duty window")
    assert "CFR" not in text and "Relay to D-03" in text and "$286" in text
    assert "On time · 22 min spare" in text and "Legal time left*\\n64 min" in text  # arrival/slack and legal hours margin
    assert "Swap to D-09" in text and "$411" in text and "9h 23m late" in text  # alternatives
    actions = next(b for b in blocks if b["type"] == "actions")["elements"]
    ids = [a["action_id"] for a in actions]
    assert ids == ["dg_approve", "dg_options", "dg_explain", "dg_link"]
    approve = actions[0]
    assert approve["value"] == inc["recommended_plan_id"] and "confirm" in approve
    assert actions[-1]["url"].endswith("/#/incident/DG-I-0042")
    assert all(len(b.get("text", {}).get("text", "")) < 3000 for b in blocks) and len(blocks) <= 6
    opts = option_blocks(inc)
    btns = [b["accessory"] for b in opts if "accessory" in b]
    assert len(btns) == 3 and all(x["action_id"].startswith("dg_approve") and "confirm" in x for x in btns)


def test_flush_posts_each_incident_once_then_edits_in_place(env):
    store, clock, engine = env
    b = bridge(env)
    assert b.flush() == 1  # DG-I-0041 (stale feed) from the seed sweep
    delay(engine)
    assert b.flush() == 1  # DG-I-0042
    assert b.flush() == 0 and b.flush() == 0  # nothing new: no spam
    c = b.app.client
    assert [p["text"].split(" ")[0] for p in c.posts] == ["DG-I-0041", "DG-I-0042"]
    assert all(r["status"] == "sent" for r in store.outbox_list(50) if r["channel"] == "dispatcher")
    # A new revision edits the original card and adds one thread note.
    inc = store.get_incident("DG-I-0042")
    inc["revision"] += 1
    store.put_incident(inc)
    assert b.flush() == 1 and b.flush() == 0
    assert c.updates[-1]["ts"] == "2.000" and c.posts[-1]["thread_ts"] == "2.000" and len(c.posts) == 3


def test_closed_incidents_are_not_posted_fresh(env):
    store, _, engine = env
    delay(engine)
    inc = store.get_incident("DG-I-0042")
    inc["status"] = "RESOLVED"
    store.put_incident(inc)
    b = bridge(env)
    b.flush()
    assert all("DG-I-0042" not in p["text"] for p in b.app.client.posts)


def test_approve_button_goes_through_the_gate_and_updates_card(env):
    store, _, engine = env
    b = bridge(env)
    delay(engine)
    b.flush()
    pid = store.get_incident("DG-I-0042")["recommended_plan_id"]
    c = b.app.client
    n_posts = len(c.posts)
    b.app.click("dg_approve", pid)
    assert store.get_plan(pid)[1] == "EXECUTED"
    inc = store.get_incident("DG-I-0042")
    assert inc["status"] == "EXECUTED" and "slack:U0C628NKALF" in inc["history"][-1]["what"]
    assert any(a["kind"] == "plan_approved" and a["data"]["actor"].startswith("slack:U0C628NKALF") for a in store.audit_recent(50))
    reply = c.posts[n_posts]
    assert reply["thread_ts"] == "2.000" and "approved" in reply["text"] and "re-checked (PASS)" in reply["text"]
    card = c.updates[-1]["blocks"]
    assert not any(e["action_id"].startswith("dg_approve") for blk in card if blk["type"] == "actions" for e in blk["elements"])
    assert len(c.posts) == n_posts + 1  # the card edit adds no extra thread note
    assert b.flush() == 0
    # Double click: idempotent, nothing re-executed.
    b.app.click("dg_approve", pid)
    assert "already approved" in c.posts[-1]["text"]


def test_option_approve_and_non_approver_blocked(env):
    store, _, engine = env
    b = bridge(env, approvers="U0C628NKALF")
    delay(engine)
    b.flush()
    inc = store.get_incident("DG-I-0042")
    b.app.click("dg_options", inc["id"])
    opts = b.app.client.posts[-1]
    assert opts["thread_ts"] == "2.000" and len([x for x in opts["blocks"] if "accessory" in x]) == 3
    swap = inc["plans"][1]["plan_id"]
    b.app.click("dg_approve_1", swap, user="U0INTRUDER")
    assert "not on the approver list" in b.app.client.posts[-1]["text"] and store.get_plan(swap)[1] == "PROPOSED"
    b.app.click("dg_approve_1", swap)
    assert store.get_plan(swap)[1] == "EXECUTED"


def test_failed_revalidation_is_reported_not_executed(env, monkeypatch):
    store, _, engine = env
    b = bridge(env)
    delay(engine)
    pid = store.get_incident("DG-I-0042")["recommended_plan_id"]

    def boom(*a, **k):
        raise service.ApprovalError("revalidation failed (FAIL); plan not executed")
    monkeypatch.setattr(service, "execute_approved_plan", boom)
    out = approve_from_slack(b.ctx, pid, "U0C628NKALF")
    assert not out["ok"] and "Not executed" in out["text"]


def test_slack_approval_actor_must_be_human(env):
    store, clock, engine = env
    delay(engine)
    pid = store.get_incident("DG-I-0042")["recommended_plan_id"]
    tok = service.approve_plan(store, clock, pid, "slack:U123:alex")
    res = service.execute_approved_plan(store, clock, pid, tok["approval_token"], f"slack:{pid}")
    assert res["approved_by"] == "slack:U123:alex"
    again = service.execute_approved_plan(store, clock, pid, tok["approval_token"], f"slack:{pid}")
    assert again.get("duplicate")


def test_bridge_disabled_without_tokens(env, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    b = SlackBridge(SimpleNamespace(store=env[0], clock=env[1]))
    b.start()
    assert b.status["enabled"] is False and "disabled" in b.status["last_error"]


def test_slack_routes_card_preview(env, monkeypatch):
    from fastapi.testclient import TestClient
    import app.api.main as m
    store, clock, engine = env
    delay(engine)
    monkeypatch.setattr(m, "ctx", SimpleNamespace(store=store, clock=clock, slack=None))
    client = TestClient(m.app, client=("127.0.0.1", 50000))  # no lifespan: no runtime, no sockets
    r = client.get("/api/slack/incidents/DG-I-0042/card")
    assert r.status_code == 200 and r.json()["card"]["blocks"][0]["type"] == "header"
    assert client.get("/api/slack/incidents/NOPE/card").status_code == 404
    assert client.post("/api/slack/flush").status_code == 409  # bridge not connected
    assert client.get("/api/slack/status").json()["enabled"] is False
