"""Append-only JSONL audit trail (spec sec. 13)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

AUDIT_DIR = Path(__file__).resolve().parents[2] / "audit"


def audit(event_type: str, **fields) -> None:
    AUDIT_DIR.mkdir(exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        **fields,
    }
    with open(AUDIT_DIR / "audit.jsonl", "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
