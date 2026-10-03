"""Render core objects as Slack Block Kit messages.

The incident message is the product's money shot: verdict, cause, ranked
priced options, and an approval gate. Keep it scannable in the 2-minute video.
"""

from __future__ import annotations

from app.core.models import Incident, IncidentStatus, RecoveryPlan

SEVERITY_EMOJI = {"LOW": "ℹ️", "MEDIUM": "🟡", "HIGH": "🚨", "CRITICAL": "🔴"}


def _fmt_eta(plan: RecoveryPlan) -> str:
    if plan.delivery_eta is None:
        return "—"
    eta = f"<!date^{int(plan.delivery_eta.timestamp())}^{{time}}|{plan.delivery_eta.strftime('%H:%M')} UTC>"
    slack = plan.appointment_slack_minutes
    if slack is None:
        return eta
    if slack >= 0:
        return f"{eta} (on time, {slack} min slack)"
    hours, mins = divmod(-slack, 60)
    late = f"{hours}h {mins:02d}m" if hours else f"{mins} min"
    return f"{eta} ({late} past window)"


def _plan_lines(plan: RecoveryPlan) -> str:
    return (
        f"*{plan.title}* — *+${plan.incremental_cost:,.0f}*\n"
        f"Delivery: {_fmt_eta(plan)}  ·  Legal reserve: {plan.hos_reserve_minutes} min"
        f"  ·  Risk: {plan.risk_score}/100"
    )


def incident_blocks(incident: Incident) -> list[dict]:
    emoji = SEVERITY_EMOJI.get(incident.severity.value, "🚨")
    rec = incident.recommended
    failures = ", ".join(
        f"`{f.code}` (+{f.excess_minutes} min)" if f.excess_minutes else f"`{f.code}`"
        for f in incident.compliance.hard_failures
    ) or "none"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} INCIDENT {incident.incident_id} — {incident.severity.value}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"{incident.summary}\n"
                    f"*Cause:* {incident.cause}\n"
                    f"*Customer impact:* {incident.customer_impact}\n"
                    f"*Compliance:* `{incident.compliance.verdict.value}` — {failures}"
                    f"  ·  ruleset `{incident.compliance.ruleset}`"
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"⭐ *Recommended*\n{_plan_lines(rec)}"},
        },
    ]

    for plan in incident.plans:
        if plan.plan_id == incident.recommended_plan_id:
            continue
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": _plan_lines(plan)}}
        )

    if incident.status == IncidentStatus.OPEN:
        buttons = []
        for plan in incident.plans:
            is_rec = plan.plan_id == incident.recommended_plan_id
            buttons.append(
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": ("✅ Approve: " if is_rec else "Approve: ")
                        + f"{plan.title} (+${plan.incremental_cost:,.0f})",
                    },
                    **({"style": "primary"} if is_rec else {}),
                    "action_id": f"approve_plan:{plan.plan_id}",
                    "value": f"{incident.incident_id}|{plan.plan_id}",
                }
            )
        buttons.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Reject all"},
                "style": "danger",
                "action_id": "reject_incident",
                "value": incident.incident_id,
                "confirm": {
                    "title": {"type": "plain_text", "text": "Reject all options?"},
                    "text": {
                        "type": "mrkdwn",
                        "text": "The incident stays open for manual handling and the override is logged.",
                    },
                    "confirm": {"type": "plain_text", "text": "Reject"},
                    "deny": {"type": "plain_text", "text": "Cancel"},
                },
            }
        )
        blocks.append({"type": "actions", "elements": buttons})
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"Recommendation expires <!date^{int(incident.expires_at.timestamp())}"
                            f"^at {{time}}|soon>  ·  Dispatcher approval required — "
                            f"the agent never executes on its own."
                        ),
                    }
                ],
            }
        )

    return blocks


def resolution_blocks(incident: Incident, outcome_line: str) -> list[dict]:
    """Replace the actions row once the incident is decided."""
    blocks = incident_blocks(incident)  # status != OPEN drops buttons
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": outcome_line},
        }
    )
    return blocks
