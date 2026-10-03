"""Dispatch Guardian Slack bot — Socket Mode.

Run:  python -m app.adapters.slack.bot
Requires SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env.

Demo flow:
  1. Type `demo` in a channel the bot is in (or `@dispatch-guardian demo`).
     Incident DG-I-0042 is posted with ranked, priced options and approval
     buttons.
  2. Dispatcher clicks Approve -> execute_approved_plan() -> message updates
     with the outcome and an audit record is written.
  3. @mention the bot for driver status or a decision trace.
"""

from __future__ import annotations

import os
import re
import uuid

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.adapters.slack import renderer
from app.agent.loop import AgentLoop
from app.audit.log import audit
from app.core.client import CoreClient, FakeCore
from app.core.models import IncidentStatus

load_dotenv()

core: CoreClient = FakeCore()  # swap for HTTPCore when the GB10 API is up
agent = AgentLoop(core)

app = App(token=os.environ["SLACK_BOT_TOKEN"])


def post_incident(client, channel: str) -> None:
    incident = core.open_demo_incident()  # type: ignore[attr-defined]
    response = client.chat_postMessage(
        channel=channel,
        text=f"Incident {incident.incident_id}: {incident.summary}",
        blocks=renderer.incident_blocks(incident),
    )
    incident.slack_channel = response["channel"]
    incident.slack_ts = response["ts"]
    audit("incident_posted", incident_id=incident.incident_id, channel=response["channel"])


@app.event("app_mention")
def on_mention(event, client, say):
    text = re.sub(r"<@[^>]+>", "", event.get("text", "")).strip()
    if text.lower().startswith("demo"):
        post_incident(client, event["channel"])
        return
    say(text=agent.handle(text), thread_ts=event.get("thread_ts") or event["ts"])


@app.message(re.compile(r"^demo$", re.IGNORECASE))
def on_demo_keyword(message, client):
    post_incident(client, message["channel"])


@app.action(re.compile(r"approve_plan:.*"))
def on_approve(ack, body, client, action):
    ack()
    incident_id, plan_id = action["value"].split("|", 1)
    incident = core.get_incident(incident_id)
    user = body["user"]["id"]

    if incident is None:
        client.chat_postEphemeral(
            channel=body["channel"]["id"], user=user,
            text=f"Incident {incident_id} not found (was the bot restarted?).",
        )
        return

    result = core.execute_approved_plan(
        plan_id=plan_id,
        approval_token=incident.approval_token,
        idempotency_key=f"slack:{incident_id}:{plan_id}:{incident.slack_ts}",
    )
    audit(
        "approval_attempt",
        incident_id=incident_id, plan_id=plan_id, actor=user,
        ok=result.ok, message=result.message,
    )

    if result.ok:
        plan = incident.plan(plan_id)
        outcome = (
            f"✅ *Approved by <@{user}>* — {plan.title} executed. "
            f"{result.message}. Decision trace: `get_decision_trace {incident_id}`"
        )
    else:
        outcome = f"⚠️ Approval by <@{user}> failed: {result.message}"

    client.chat_update(
        channel=incident.slack_channel,
        ts=incident.slack_ts,
        text=f"Incident {incident_id} — {result.message}",
        blocks=renderer.resolution_blocks(incident, outcome),
    )


@app.action("reject_incident")
def on_reject(ack, body, client, action):
    ack()
    incident_id = action["value"]
    user = body["user"]["id"]
    incident = core.get_incident(incident_id)
    if incident is None:
        return

    core.record_override(incident_id, actor=user, reason="dispatcher rejected all options")
    incident.status = IncidentStatus.REJECTED
    audit("incident_rejected", incident_id=incident_id, actor=user)

    client.chat_update(
        channel=incident.slack_channel,
        ts=incident.slack_ts,
        text=f"Incident {incident_id} rejected",
        blocks=renderer.resolution_blocks(
            incident,
            f"🚫 *All options rejected by <@{user}>.* Incident remains open for manual handling; override logged.",
        ),
    )


def main() -> None:
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    print("Dispatch Guardian connected via Socket Mode. Type `demo` in a channel with the bot.")
    handler.start()


if __name__ == "__main__":
    main()
