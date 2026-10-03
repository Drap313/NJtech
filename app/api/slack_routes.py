"""Slack alert-bridge endpoints: status, Block Kit previews, manual (re)post.

Off-box callers need DG_API_TOKEN (main.py middleware). None of these approve
anything; approvals only happen through Slack button clicks (approve_from_slack)
or the existing approval endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..integrations.slack_bridge import incident_blocks, option_blocks

router = APIRouter(prefix="/api/slack", tags=["slack"])


def _ctx():
    from .main import C  # late import: main includes this router at import time
    return C()


def _incident(incident_id: str) -> dict:
    inc = _ctx().store.get_incident(incident_id)
    if not inc:
        raise HTTPException(404, "unknown incident")
    return inc


def _bridge():
    sl = getattr(_ctx(), "slack", None)
    if not sl or not sl.enabled or sl.app is None or not sl.channel_id:
        raise HTTPException(409, "Slack alerts bridge is not connected (set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_CHANNEL)")
    return sl


@router.get("/status")
def slack_status():
    c = _ctx()
    sl = getattr(c, "slack", None)
    cards = c.store.meta_get("slack_cards", {})
    return {**(sl.status if sl else {"enabled": False}), "cards": cards}


@router.get("/incidents/{incident_id}/card")
def card(incident_id: str):
    """Block Kit JSON for the incident card and its options message (paste into Slack's Block Kit Builder)."""
    import os
    inc = _incident(incident_id)
    return {"card": {"blocks": incident_blocks(inc, os.environ.get("DG_PUBLIC_URL"))}, "options": {"blocks": option_blocks(inc)}}


@router.post("/incidents/{incident_id}/post")
def post(incident_id: str):
    """Post (or edit in place) this incident's card now."""
    sl = _bridge()
    sl.post_incident(_incident(incident_id))
    return {"ok": True, "card": _ctx().store.meta_get("slack_cards", {}).get(incident_id)}


@router.post("/flush")
def flush():
    """Post any new incidents / card updates now instead of waiting for the 3 s loop."""
    return {"posted": _bridge().flush()}
