"""Append-only audit: every record goes to the database and a JSONL file."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from ..db import Store

_lock = threading.Lock()


def attach_jsonl(store: Store, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def sink(rec: dict) -> None:
        with _lock, path.open("a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    store.audit_sink = sink


def decision_trace(store: Store, incident_id: str) -> dict:
    inc = store.get_incident(incident_id)
    if inc is None:
        return {"error": f"unknown incident {incident_id}"}
    records = store.audit_for(f"incident:{incident_id}")
    asg_records = [r for r in store.audit_for(f"assignment:{inc['assignment_id']}") if r["kind"] in ("execution", "evaluation_stored")]
    events = [e for e in store.all_events() if e["event_id"] in set(inc.get("trigger_events", []))]
    return {
        "incident_id": incident_id,
        "status": inc["status"],
        "policy_versions": inc.get("policy_versions"),
        "trigger_events": events,
        "inputs_snapshot": inc.get("inputs_snapshot"),
        "evaluation": inc.get("evaluation"),
        "plans": [{k: p[k] for k in ("plan_id", "title", "compliance_verdict", "recheck", "incremental_cost", "expires_at")} for p in inc.get("plans", [])],
        "audit": records + asg_records,
    }
