"""Slack bridge (Socket Mode: outbound connection only, no public URL needed).

- Posts new and updated incidents from the outbox to a channel, with buttons.
- Approve / Reject / Show options / Explain buttons act through the same
  approval gate as the dashboard; the actor is the Slack user who clicked.
- @mentions and DMs go to the local agent (read-only tools).
- Driver and customer messages stay in the outbox; customer messages are
  never sent from Slack without approval.

Enabled when SLACK_BOT_TOKEN (xoxb-) and SLACK_APP_TOKEN (xapp-) are set.
SLACK_CHANNEL: channel ID or name to post incidents in.
SLACK_APPROVERS: optional comma-separated Slack user IDs allowed to approve.
DG_PUBLIC_URL: optional dashboard link shown on messages.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("dispatch.slack")
ET = ZoneInfo("America/New_York")
SEV_EMOJI = {"CRITICAL": ":rotating_light:", "HIGH": ":red_circle:", "MEDIUM": ":large_orange_circle:", "LOW": ":white_circle:"}


def _t(iso: str | None) -> str:
    return datetime.fromisoformat(iso).astimezone(ET).strftime("%a %H:%M %Z") if iso else "n/a"


def _hm(m) -> str:
    if m is None:
        return "n/a"
    a = abs(int(m))
    s = f"{a // 60}h {a % 60:02d}m" if a >= 60 else f"{a} min"
    return ("-" if m < 0 else "") + s


def _slack_line(p: dict) -> str:
    s = p["appointment_slack_minutes"]
    when = f"on time, {s} min slack" if s is not None and s >= 0 else f"late {_hm(s)}"
    return f"*{p['title']}* · {when} · reserve {_hm(p['hos_reserve_minutes'])} · +${p['incremental_cost']:,.0f} · risk {p['risk_score']}"


def incident_blocks(inc: dict, dashboard: str | None = None) -> list[dict]:
    """Block Kit message for an incident (pure function, unit-testable)."""
    sev = inc["severity"]
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{inc['id']} · {sev}"[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"{SEV_EMOJI.get(sev, '')} *{inc['title']}*\n*Cause:* {inc['cause']}\n*Customer impact:* {inc['customer_impact']}"}},
    ]
    plans = inc.get("plans") or []
    open_ = inc["status"] == "OPEN"
    if plans:
        rec = plans[0]
        blocks.append({"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Recommended*\n{rec['title']}"},
            {"type": "mrkdwn", "text": f"*Delivery*\n{_t(rec['delivery_eta'])} ({_hm(rec['appointment_slack_minutes'])} slack)"},
            {"type": "mrkdwn", "text": f"*Legal reserve*\n{_hm(rec['hos_reserve_minutes'])}"},
            {"type": "mrkdwn", "text": f"*Incremental cost*\n${rec['incremental_cost']:,.0f}"},
        ]})
        if len(plans) > 1:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Alternatives*\n" + "\n".join("• " + _slack_line(p) for p in plans[1:])}})
        exp = int((datetime.fromisoformat(rec["expires_at"]) - datetime.fromisoformat(inc["updated_at"])).total_seconds() // 60)
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"Compliance re-checked: {rec.get('recheck', {}).get('verdict', 'n/a')} · expires {_t(rec['expires_at'])} (~{exp} min after this update) · rules decide legality; approval required"}]})
    elif inc.get("auto_actions"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "Automatic: " + "; ".join(a["what"] for a in inc["auto_actions"])}]})
    actions = []
    if open_ and plans:
        actions.append({"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve recommended"},
                        "action_id": "dg_approve", "value": plans[0]["plan_id"],
                        "confirm": {"title": {"type": "plain_text", "text": "Approve plan?"},
                                    "text": {"type": "mrkdwn", "text": f"{plans[0]['title']}\nThe plan is re-validated before it is committed."},
                                    "confirm": {"type": "plain_text", "text": "Approve & execute"}, "deny": {"type": "plain_text", "text": "Cancel"}}})
        actions.append({"type": "button", "text": {"type": "plain_text", "text": "Show options"}, "action_id": "dg_options", "value": inc["id"]})
    actions.append({"type": "button", "text": {"type": "plain_text", "text": "Explain"}, "action_id": "dg_explain", "value": inc["id"]})
    if dashboard:
        actions.append({"type": "button", "text": {"type": "plain_text", "text": "Open dashboard"}, "action_id": "dg_link",
                        "url": f"{dashboard.rstrip('/')}/#/incidents/{inc['id']}"})
    blocks.append({"type": "actions", "elements": actions})
    if inc["status"] != "OPEN":
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{inc['status']}* · " + (inc.get("history") or [{}])[-1].get("what", "")}]})
    return blocks


def option_blocks(inc: dict) -> list[dict]:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*Options for {inc['id']}* (ranked: compliance → on time → cost → risk)"}}]
    for i, p in enumerate(inc.get("plans") or []):
        lines = [_slack_line(p)] + [f"  {c['component']}: ${c['amount']:,.0f} _({c['basis']})_" for c in p["cost_breakdown"]]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)[:2900]},
                       "accessory": {"type": "button", "style": "primary" if i == 0 else None, "text": {"type": "plain_text", "text": "Approve"},
                                     "action_id": f"dg_approve_{i}", "value": p["plan_id"]} if inc["status"] == "OPEN" else None})
        if blocks[-1]["accessory"] is None:
            blocks[-1].pop("accessory")
        elif blocks[-1]["accessory"]["style"] is None:
            blocks[-1]["accessory"].pop("style")
    if inc.get("rejected"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "Excluded by hard rules: " +
                       "; ".join(f"{r['title']} ({', '.join(r['codes'])})" for r in inc["rejected"][:6])[:2900]}]})
    return blocks


class SlackBridge:
    def __init__(self, ctx):
        self.ctx = ctx
        self.bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        self.app_token = os.environ.get("SLACK_APP_TOKEN", "")
        self.channel_cfg = os.environ.get("SLACK_CHANNEL", "")
        self.approvers = {u.strip() for u in os.environ.get("SLACK_APPROVERS", "").split(",") if u.strip()}
        self.dashboard = os.environ.get("DG_PUBLIC_URL")
        self.enabled = bool(self.bot_token and self.app_token and self.channel_cfg)
        self.status = {"enabled": self.enabled, "connected": False, "channel": None, "posted": 0, "last_error": None}
        self.app = None
        self.channel_id: str | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not self.enabled:
            self.status["last_error"] = "disabled: set SLACK_BOT_TOKEN, SLACK_APP_TOKEN and SLACK_CHANNEL"
            return
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler
        self.app = App(token=self.bot_token)
        self._register(self.app)
        try:
            self.channel_id = self._resolve_channel(self.channel_cfg)
            self.status["channel"] = self.channel_id
        except Exception as e:
            self.status["last_error"] = f"channel lookup failed: {e}"
            log.exception("slack channel lookup failed")
            return
        handler = SocketModeHandler(self.app, self.app_token)
        threading.Thread(target=self._run_socket, args=(handler,), name="slack-socket", daemon=True).start()
        threading.Thread(target=self._outbox_loop, name="slack-outbox", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run_socket(self, handler) -> None:
        try:
            self.status["connected"] = True
            handler.start()
        except Exception as e:
            self.status.update(connected=False, last_error=f"socket mode: {e}")
            log.exception("slack socket mode stopped")

    def _resolve_channel(self, cfg: str) -> str:
        if re.fullmatch(r"[CG][A-Z0-9]{6,}", cfg):
            return cfg
        name = cfg.lstrip("#")
        cursor = None
        while True:
            r = self.app.client.conversations_list(types="public_channel,private_channel", limit=500, cursor=cursor)
            for c in r["channels"]:
                if c["name"] == name:
                    return c["id"]
            cursor = r.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                raise ValueError(f"channel #{name} not found or bot not invited")

    # ------------------------------------------------------------------ outbound
    def _outbox_loop(self) -> None:
        while not self._stop.wait(3):
            try:
                self.flush()
            except Exception as e:
                self.status["last_error"] = f"post failed: {e}"
                log.exception("slack outbox flush failed")

    def flush(self) -> int:
        store = self.ctx.store
        sent = 0
        for row in reversed(store.outbox_list(50, "queued")):
            if row["channel"] != "dispatcher" or not row["incident_id"]:
                continue
            inc = store.get_incident(row["incident_id"])
            if inc is None:
                continue
            self.post_incident(inc)
            store.outbox_set_status(row["id"], "sent")
            sent += 1
        return sent

    def post_incident(self, inc: dict) -> None:
        store, client = self.ctx.store, self.app.client
        threads = store.meta_get("slack_threads", {})
        blocks = incident_blocks(inc, self.dashboard)
        text = f"{inc['id']} {inc['severity']}: {inc['title']}"
        ts = threads.get(inc["id"])
        if ts:  # update the original card and note the revision in its thread
            client.chat_update(channel=self.channel_id, ts=ts, text=text, blocks=blocks)
            client.chat_postMessage(channel=self.channel_id, thread_ts=ts, text=f"Updated (rev {inc['revision']}): {inc['cause']}")
        else:
            r = client.chat_postMessage(channel=self.channel_id, text=text, blocks=blocks)
            threads[inc["id"]] = r["ts"]
            store.meta_set("slack_threads", threads)
        self.status["posted"] += 1
        store.audit("slack_posted", f"incident:{inc['id']}", {"revision": inc["revision"], "channel": self.channel_id}, self.ctx.clock.now().isoformat())

    def _refresh_card(self, incident_id: str) -> None:
        inc = self.ctx.store.get_incident(incident_id)
        ts = self.ctx.store.meta_get("slack_threads", {}).get(incident_id)
        if inc and ts:
            self.app.client.chat_update(channel=self.channel_id, ts=ts, text=f"{inc['id']} {inc['status']}", blocks=incident_blocks(inc, self.dashboard))

    def _incident_for_thread(self, ts: str | None) -> str | None:
        if not ts:
            return None
        for iid, t in self.ctx.store.meta_get("slack_threads", {}).items():
            if t == ts:
                return iid
        return None

    # ------------------------------------------------------------------ inbound
    def _register(self, app) -> None:
        from ..incidents import service

        def actor_of(body) -> str:
            u = body["user"]
            return f"slack:{u['id']}:{u.get('username') or u.get('name', '')}"

        def reply(body, text, blocks=None):
            msg = body.get("message") or {}
            ts = msg.get("thread_ts") or msg.get("ts")
            ch = (body.get("channel") or {}).get("id") or self.channel_id
            self.app.client.chat_postMessage(channel=ch, thread_ts=ts, text=text, blocks=blocks)

        @app.action(re.compile(r"^dg_approve(_\d+)?$"))
        def on_approve(ack, body, action):
            ack()
            pid = action["value"]
            uid = body["user"]["id"]
            if self.approvers and uid not in self.approvers:
                reply(body, f"<@{uid}> is not on the approver list; plan {pid} was not approved.")
                return
            c = self.ctx
            try:
                tok = service.approve_plan(c.store, c.clock, pid, actor_of(body))
                res = service.execute_approved_plan(c.store, c.clock, pid, tok["approval_token"], f"slack:{pid}")
                c.engine.sweep()
                rv = res["revalidation"]
                reply(body, f":white_check_mark: <@{uid}> approved *{pid}*. Re-validated {rv['verdict']}, delivery {_t(rv['delivery_eta'])} "
                            f"({_hm(rv['appointment_slack_minutes'])} slack).\n" + "\n".join("• " + x for x in res["changes"]))
                self._refresh_card(res["incident_id"])
            except service.ApprovalError as e:
                reply(body, f":no_entry: Not executed: {e}")

        @app.action("dg_options")
        def on_options(ack, body, action):
            ack()
            inc = self.ctx.store.get_incident(action["value"])
            if inc:
                reply(body, f"Options for {inc['id']}", option_blocks(inc))

        @app.action("dg_explain")
        def on_explain(ack, body, action):
            ack()
            iid = action["value"]
            reply(body, f":hourglass_flowing_sand: Asking the local model (Qwen3.6 on the GB10) to explain {iid}…")
            from ..agent.runner import explain_incident
            try:
                out = explain_incident(self.ctx.tools, self.ctx.llm, iid)
                reply(body, f"{out['answer']}\n_{out['model']} · {out['elapsed_s']}s · local_")
            except Exception as e:
                reply(body, f"Local model unavailable: {e}")

        @app.action("dg_link")
        def on_link(ack):
            ack()

        def answer(text: str, channel: str, thread_ts: str | None, user: str):
            from ..agent.runner import run_agent
            from ..agent.tools import compact
            iid = self._incident_for_thread(thread_ts)
            m = re.search(r"DG-I-\d{4}", text)
            iid = iid or (m.group(0) if m else None)
            ctxt = compact(self.ctx.tools.get_incident(iid), 8000) if iid and self.ctx.store.get_incident(iid) else None
            try:
                out = run_agent(self.ctx.tools, self.ctx.llm, text, context=ctxt)
                tools = " → ".join(s["tool"] + (" (blocked: approval required)" if s["blocked"] else "") for s in out["steps"]) or "no tools"
                self.app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"{out['answer']}\n_{tools} · {out['elapsed_s']}s · local model_")
            except Exception as e:
                self.app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=f"Local model unavailable: {e}")

        @app.event("app_mention")
        def on_mention(event):
            text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
            answer(text or "What needs my attention right now?", event["channel"], event.get("thread_ts") or event["ts"], event.get("user", ""))

        @app.event("message")
        def on_dm(event):
            if event.get("channel_type") == "im" and not event.get("bot_id") and not event.get("subtype"):
                answer(event.get("text", ""), event["channel"], event.get("thread_ts"), event.get("user", ""))
