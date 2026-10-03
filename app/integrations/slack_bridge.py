"""Slack alerts bridge (Socket Mode: outbound connection only, no public URL needed).

Runs on its own Slack app ("Dispatch Guardian Alerts"); conversation stays with the
OpenClaw bot. Two bots, one channel.

- Each new OPEN incident is posted once as a compact card. Later revisions and
  status changes edit that card in place and add one note in its thread.
- Approve recommended / See options (each with its own Approve) / Explain /
  Open dashboard. Every approval goes through approve_from_slack(): the same
  approve -> re-validate -> execute gate as the dashboard, actor "slack:<user id>".
- @mentions and DMs (only if the app subscribes to them) go to the local agent.

Enabled when SLACK_BOT_TOKEN (xoxb-), SLACK_APP_TOKEN (xapp-) and SLACK_CHANNEL are set.
SLACK_APPROVERS: optional comma-separated Slack user IDs allowed to approve.
DG_PUBLIC_URL: optional dashboard base URL for the "Open dashboard" button.
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


RULE_NAMES = {
    "SHIFT_LIMIT": "the 14-hour duty window",
    "DRIVE_LIMIT": "the 11-hour driving limit",
    "CYCLE_LIMIT": "the 60/70-hour weekly limit",
    "BREAK_REQUIRED": "the 30-minute break rule",
    "COMPANY_DRIVE_LIMIT": "the company driving limit",
    "COMPANY_WINDOW_LIMIT": "the company duty-window limit",
    "COMPANY_MIN_RESERVE": "the company minimum hours reserve",
}
STATUS_TEXT = {"EXECUTED": ":white_check_mark: Fixed", "RESOLVED": ":white_check_mark: Resolved on its own",
               "OVERRIDDEN": ":warning: Overridden by a dispatcher"}


def plain_cause(inc: dict) -> str:
    """The incident cause in plain words, e.g. 'D-01 would drive 41 min over the 14-hour driving window'."""
    cur = inc.get("current_plan") or {}
    fails = (cur.get("compliance") or {}).get("hard_failures") or (inc.get("evaluation") or {}).get("hard_failures") or []
    if fails:
        h = fails[0]
        rule = RULE_NAMES.get(h.get("code"))
        if rule and h.get("excess_minutes") is not None:
            who = h.get("driver_id") or "The driver"
            when = f" (from {_t(h['first_at'])})" if h.get("first_at") else ""
            return f"{who} would drive {h['excess_minutes']} min past {rule}{when} if nothing changes."
        if h.get("detail"):
            return h["detail"][0].upper() + h["detail"][1:]
    cause = re.sub(r"\s*\([^)]*CFR[^)]*\)", "", inc.get("cause") or "").strip()
    return (cause[:1].upper() + cause[1:]) if cause else "Plan changed."


def plain_impact(inc: dict) -> str:
    return (inc.get("customer_impact") or "").replace("breaking HOS", "breaking hours-of-service rules")


def _arrival(p: dict) -> str:
    s = p.get("appointment_slack_minutes")
    if s is None:
        return "arrival unknown"
    return f"On time · {s} min spare" if s >= 0 else f"{_hm(-s)} late"


def _margin(p: dict) -> str:
    r = p.get("hos_reserve_minutes")
    return "n/a" if r is None else f"{r} min" if r >= 0 else f"{-r} min over"


def _slack_line(p: dict) -> str:
    return f"*{p['title']}*: {_arrival(p)} · extra cost ${p['incremental_cost']:,.0f} · legal time left {_margin(p)}"


def _approve_button(plan: dict, action_id: str, label: str, primary: bool) -> dict:
    b = {"type": "button", "text": {"type": "plain_text", "text": label}, "action_id": action_id, "value": plan["plan_id"],
         "confirm": {"title": {"type": "plain_text", "text": "Approve this plan?"},
                     "text": {"type": "mrkdwn", "text": f"*{plan['title']}*\n{_arrival(plan)}, extra cost ${plan['incremental_cost']:,.0f}.\n"
                                                       "Dispatch Guardian re-checks the hours rules before anything changes."},
                     "confirm": {"type": "plain_text", "text": "Approve & execute"}, "deny": {"type": "plain_text", "text": "Cancel"}}}
    if primary:
        b["style"] = "primary"
    return b


def incident_blocks(inc: dict, dashboard: str | None = None) -> list[dict]:
    """Compact Block Kit card for an incident (pure function, unit-testable)."""
    sev = inc["severity"]
    open_ = inc["status"] == "OPEN"
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{inc['id']} · {sev} · Load {inc.get('load_id', '')}"[:150]}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"{SEV_EMOJI.get(sev, '')} *{inc['title']}*\n*Why:* {plain_cause(inc)}\n*Customer:* {plain_impact(inc)}")[:2900]}},
    ]
    plans = inc.get("plans") or []
    if plans and open_:
        rec = plans[0]
        blocks.append({"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Recommended fix*\n{rec['title']}"},
            {"type": "mrkdwn", "text": f"*Arrives {_t(rec['delivery_eta'])}*\n{_arrival(rec)}"},
            {"type": "mrkdwn", "text": f"*Extra cost*\n${rec['incremental_cost']:,.0f}"},
            {"type": "mrkdwn", "text": f"*Legal time left*\n{_margin(rec)}"},
        ]})
        if len(plans) > 1:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": (
                "*Alternatives*\n" + "\n".join("• " + _slack_line(p) for p in plans[1:4]))[:2900]}})
        why = f" ({rec['expiry_reason']})" if rec.get("expiry_reason") else ""
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text":
            f"Hours rules re-checked: {rec.get('recheck', {}).get('verdict', rec.get('compliance_verdict', 'n/a'))} · "
            f"offer valid until {_t(rec['expires_at'])}{why} · needs a dispatcher's approval"}]})
    elif open_ and inc.get("auto_actions"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "Done automatically: " + "; ".join(a["what"] for a in inc["auto_actions"])}]})
    if not open_:
        last = (inc.get("history") or [{}])[-1].get("what", "")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*{STATUS_TEXT.get(inc['status'], inc['status'])}*\n{last}"[:2900]}})
    actions = []
    if open_ and plans:
        actions.append(_approve_button(plans[0], "dg_approve", "Approve recommended", True))
        actions.append({"type": "button", "text": {"type": "plain_text", "text": "See options"}, "action_id": "dg_options", "value": inc["id"]})
    actions.append({"type": "button", "text": {"type": "plain_text", "text": "Explain"}, "action_id": "dg_explain", "value": inc["id"]})
    if dashboard:
        actions.append({"type": "button", "text": {"type": "plain_text", "text": "Open dashboard"}, "action_id": "dg_link",
                        "url": f"{dashboard.rstrip('/')}/#/incident/{inc['id']}"})
    blocks.append({"type": "actions", "block_id": f"dg_actions_{inc['id']}", "elements": actions})
    return blocks


def option_blocks(inc: dict) -> list[dict]:
    """Every ranked option with its own Approve button (posted in the card's thread)."""
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": f"*Options for {inc['id']}*, best first (legal → on time → cost → risk)"}}]
    open_ = inc["status"] == "OPEN"
    for i, p in enumerate(inc.get("plans") or []):
        costs = " · ".join(f"{c['component']} ${c['amount']:,.0f}" for c in p.get("cost_breakdown", []))
        tag = " _(recommended)_" if i == 0 else ""
        text = (f"*{i + 1}. {p['title']}*{tag}\nArrives {_t(p['delivery_eta'])} · {_arrival(p)} · extra cost ${p['incremental_cost']:,.0f} · "
                f"legal time left {_margin(p)} · schedule risk {p['risk_score']}" + (f"\n_{costs}_" if costs else ""))
        b = {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}
        if open_:
            b["accessory"] = _approve_button(p, f"dg_approve_{i}", "Approve", i == 0)
        blocks.append(b)
    if inc.get("rejected"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": ("Ruled out by hard rules: " +
                       "; ".join(r["title"] for r in inc["rejected"][:6]))[:2900]}]})
    return blocks


def approve_from_slack(ctx, plan_id: str, user_id: str, user_name: str = "", approvers: set[str] | None = None) -> dict:
    """The only Slack approval path: same gate as the dashboard (approve -> re-validate -> execute).
    Returns {"ok": bool, "text": str, "incident_id": str | None}."""
    from ..incidents import service
    if approvers and user_id not in approvers:
        return {"ok": False, "text": f"<@{user_id}> is not on the approver list; {plan_id} was not approved.", "incident_id": None}
    got = ctx.store.get_plan(plan_id)
    iid = got[0]["incident_id"] if got else None
    if got and got[1] == "EXECUTED":
        return {"ok": False, "text": f"{plan_id} was already approved and executed.", "incident_id": iid}
    actor = f"slack:{user_id}" + (f":{user_name}" if user_name else "")
    try:
        tok = service.approve_plan(ctx.store, ctx.clock, plan_id, actor)
        res = service.execute_approved_plan(ctx.store, ctx.clock, plan_id, tok["approval_token"], f"slack:{plan_id}")
    except service.ApprovalError as e:
        return {"ok": False, "text": f":no_entry: Not executed: {e}", "incident_id": iid}
    if getattr(ctx, "engine", None):
        ctx.engine.sweep()
    rv = res["revalidation"]
    s = rv["appointment_slack_minutes"]
    when = f"on time · {s} min spare" if s is not None and s >= 0 else f"{_hm(-(s or 0))} late"
    return {"ok": True, "incident_id": res["incident_id"], "result": res,
            "text": f":white_check_mark: <@{user_id}> approved *{plan_id}*. Hours rules re-checked ({rv['verdict']}); "
                    f"arrives {_t(rv['delivery_eta'])}, {when}.\n" + "\n".join("• " + x for x in res["changes"])}


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
        self._lock = threading.RLock()

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
        """Post each new OPEN incident once; edit the card (plus one thread note) when its revision or status changes.
        Driven by the incident table, not by outbox delivery state, so it cannot miss or repeat an incident."""
        store = self.ctx.store
        with self._lock:
            sent = self._flush_cards(store)
        # Dispatcher notifications are delivered by the cards; mark them so chat feeds don't repeat them.
        cards = store.meta_get("slack_cards", {})
        for row in store.outbox_list(200, "queued"):
            if row["channel"] == "dispatcher" and row["incident_id"] in cards:
                store.outbox_set_status(row["id"], "sent")
        return sent

    def _flush_cards(self, store) -> int:
        cards = store.meta_get("slack_cards", {})
        sent = 0
        for inc in reversed(store.list_incidents()):
            seen = cards.get(inc["id"])
            if seen is None and inc["status"] != "OPEN":
                continue
            if seen and seen.get("rev") == inc["revision"] and seen.get("status") == inc["status"]:
                continue
            self._post_incident(inc, True)
            sent += 1
        return sent

    def post_incident(self, inc: dict, note: bool = True) -> None:
        with self._lock:
            self._post_incident(inc, note)

    def _post_incident(self, inc: dict, note: bool) -> None:
        store, client = self.ctx.store, self.app.client
        cards = store.meta_get("slack_cards", {})
        blocks = incident_blocks(inc, self.dashboard)
        text = f"{inc['id']} {inc['severity']}: {inc['title']}"
        seen = cards.get(inc["id"])
        if seen:  # edit the original card; note what changed in its thread (once per change)
            client.chat_update(channel=self.channel_id, ts=seen["ts"], text=text, blocks=blocks)
            msg = None
            if not note:
                pass
            elif inc["status"] != seen.get("status") and inc["status"] != "OPEN":
                msg = f"{STATUS_TEXT.get(inc['status'], inc['status'])}: " + (inc.get("history") or [{}])[-1].get("what", "")
            elif inc["revision"] != seen.get("rev"):
                msg = f"Updated (revision {inc['revision']}): {plain_cause(inc)}"
            if msg:
                client.chat_postMessage(channel=self.channel_id, thread_ts=seen["ts"], text=msg)
            ts = seen["ts"]
        else:
            r = client.chat_postMessage(channel=self.channel_id, text=text, blocks=blocks)
            ts = r["ts"]
        cards[inc["id"]] = {"ts": ts, "rev": inc["revision"], "status": inc["status"]}
        store.meta_set("slack_cards", cards)
        self.status["posted"] += 1
        store.audit("slack_posted", f"incident:{inc['id']}", {"revision": inc["revision"], "status": inc["status"], "channel": self.channel_id},
                    self.ctx.clock.now().isoformat())

    def _refresh_card(self, incident_id: str) -> None:
        inc = self.ctx.store.get_incident(incident_id)
        if inc and incident_id in self.ctx.store.meta_get("slack_cards", {}):
            self.post_incident(inc, note=False)  # the approval reply already says what happened

    def _incident_for_thread(self, ts: str | None) -> str | None:
        if not ts:
            return None
        for iid, card in self.ctx.store.meta_get("slack_cards", {}).items():
            if card["ts"] == ts:
                return iid
        return None

    # ------------------------------------------------------------------ inbound
    def _register(self, app) -> None:
        def reply(body, text, blocks=None):
            msg = body.get("message") or {}
            ts = msg.get("thread_ts") or msg.get("ts")
            ch = (body.get("channel") or {}).get("id") or self.channel_id
            self.app.client.chat_postMessage(channel=ch, thread_ts=ts, text=text, blocks=blocks)

        @app.action(re.compile(r"^dg_approve(_\d+)?$"))
        def on_approve(ack, body, action):
            ack()
            u = body["user"]
            out = approve_from_slack(self.ctx, action["value"], u["id"], u.get("username") or u.get("name", ""), self.approvers)
            reply(body, out["text"])
            if out["ok"]:
                self._refresh_card(out["incident_id"])

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
