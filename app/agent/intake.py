"""Unstructured intake: free text (vLLM, schema-constrained) and printed
documents/photos (Ollama vision). Output is a proposed Event with provenance and
confidence; it is only submitted when explicitly requested and confident."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from ..state import Fleet
from .llm import LocalLLM, LocalVision

INTAKE_TYPES = ["PICKUP_DELAYED", "DRIVER_DELAY_REPORTED", "DELIVERY_WINDOW_CHANGED", "VEHICLE_UNAVAILABLE",
                "TRAILER_UNAVAILABLE", "DRIVER_DECLINED", "DRIVER_ACCEPTED", "ROUTE_TIME_CHANGED", "CUSTOMER_MESSAGE_RECEIVED", "NONE"]

SCHEMA = {
    "type": "object",
    "properties": {
        "event_type": {"type": "string", "enum": INTAKE_TYPES},
        "load_id": {"type": ["string", "null"]},
        "driver_id": {"type": ["string", "null"]},
        "unit_id": {"type": ["string", "null"]},
        "delay_minutes": {"type": ["integer", "null"]},
        "new_window_start": {"type": ["string", "null"], "description": "ISO 8601 with offset"},
        "new_window_end": {"type": ["string", "null"]},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["event_type", "load_id", "driver_id", "unit_id", "delay_minutes", "new_window_start", "new_window_end", "reason", "confidence"],
}


def _context(fleet: Fleet, now: datetime) -> str:
    rows = []
    for a in sorted(fleet.assignments.values(), key=lambda a: a.id):
        if a.status in ("COMPLETED", "CANCELLED"):
            continue
        l = fleet.loads[a.load_id]
        rows.append(f"{l.id}: assignment {a.id}, driver {a.driver_id}, tractor {a.tractor_id}, trailer {a.trailer_id}, "
                    f"{fleet.net.loc(l.origin_id).name} -> {fleet.net.loc(l.destination_id).name}, stage {a.progress.stage}")
    return f"Current time {now.isoformat()} (America/New_York unless stated).\nActive loads:\n" + "\n".join(rows)


INSTRUCTIONS = ("Convert the dispatch input into exactly one structured event. Map shipper/dock delays to PICKUP_DELAYED, "
                "driver-reported en-route delays to DRIVER_DELAY_REPORTED, appointment changes to DELIVERY_WINDOW_CHANGED, "
                "breakdowns to VEHICLE_UNAVAILABLE/TRAILER_UNAVAILABLE. Resolve IDs only from the active loads list; use null "
                "when unsure. delay_minutes is the additional delay in minutes. confidence is 0-1 and must be low when the input is "
                "ambiguous. Use NONE if no operational event is described.")


def _to_event(x: dict, source: str, provenance: dict) -> dict | None:
    t = x.get("event_type")
    if t in (None, "NONE"):
        return None
    if t in ("VEHICLE_UNAVAILABLE", "TRAILER_UNAVAILABLE"):
        entity_type, entity_id = ("tractor" if t == "VEHICLE_UNAVAILABLE" else "trailer"), x.get("unit_id")
    elif x.get("load_id"):
        entity_type, entity_id = "load", x["load_id"]
    else:
        entity_type, entity_id = "driver", x.get("driver_id")
    payload: dict = {"reason": x.get("reason", ""), "provenance": provenance, "confidence": x.get("confidence")}
    if x.get("delay_minutes") is not None:
        payload["delay_minutes"] = int(x["delay_minutes"])
    if t == "ROUTE_TIME_CHANGED" and x.get("delay_minutes") is not None:
        payload["extra_minutes"] = int(x["delay_minutes"])
    if x.get("new_window_start"):
        payload["start"], payload["end"] = x["new_window_start"], x.get("new_window_end")
    if x.get("driver_id"):
        payload["driver_id"] = x["driver_id"]
    if t == "CUSTOMER_MESSAGE_RECEIVED":
        payload["text"] = x.get("reason", "")
    return {"type": t, "entity_type": entity_type, "entity_id": entity_id, "source": source, "payload": payload}


def text_to_event(llm: LocalLLM, fleet: Fleet, text: str, source: str, now: datetime) -> dict:
    msg = llm.chat([{"role": "system", "content": INSTRUCTIONS + "\n\n" + _context(fleet, now)},
                    {"role": "user", "content": text}],
                   response_format={"type": "json_schema", "json_schema": {"name": "dispatch_event", "schema": SCHEMA}},
                   max_tokens=400, temperature=0)
    x = json.loads(msg["content"])
    prov = {"kind": "text", "model": llm.model, "sha256": hashlib.sha256(text.encode()).hexdigest()[:16], "excerpt": text[:200]}
    return {"extracted": x, "proposed_event": _to_event(x, source, prov), "confidence": x.get("confidence")}


def document_to_event(vlm: LocalVision, fleet: Fleet, image: bytes, source: str, now: datetime) -> dict:
    prompt = ("This is a printed or photographed trucking document (delay notice, bill of lading, appointment confirmation, "
              "or driver log). " + INSTRUCTIONS + "\n\n" + _context(fleet, now) + "\nReturn JSON only.")
    raw = vlm.extract(image, prompt, SCHEMA)
    x = json.loads(raw["message"]["content"])
    prov = {"kind": "document", "model": vlm.model, "sha256": hashlib.sha256(image).hexdigest()[:16]}
    return {"extracted": x, "proposed_event": _to_event(x, source, prov), "confidence": x.get("confidence")}
