"""Load the deterministic synthetic fleet into a fresh store."""
from __future__ import annotations

from datetime import datetime

from .config import SYNTHETIC_DIR, load_yaml
from .db import Store
from .models import Assignment, Customer, Driver, Load, Tractor, Trailer


def seed(store: Store, path=SYNTHETIC_DIR / "fleet.yaml") -> datetime:
    raw = load_yaml(path)
    store.reset()
    for d in raw["drivers"]:
        store.put("driver", d["id"], Driver.model_validate(d))
    for t in raw["tractors"]:
        store.put("tractor", t["id"], Tractor.model_validate(t))
    for t in raw["trailers"]:
        store.put("trailer", t["id"], Trailer.model_validate(t))
    for l in raw["loads"]:
        store.put("load", l["id"], Load.model_validate(l))
    for a in raw["assignments"]:
        store.put("assignment", a["id"], Assignment.model_validate(a))
    for cid, c in raw["customers"].items():
        store.put("customer", cid, Customer.model_validate({"id": cid, **c}))
    store.meta_set("incident_seq", raw["meta"]["incident_seq_start"])
    start = datetime.fromisoformat(raw["meta"]["scenario_start"])
    store.meta_set("scenario_start", start.isoformat())
    return start
