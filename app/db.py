"""SQLite persistence: canonical entity documents, a replayable event table,
incidents, plans, approvals, executions, outbox and an append-only audit log."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (kind TEXT, id TEXT, data TEXT, updated_at TEXT, PRIMARY KEY (kind, id));
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE, idempotency_key TEXT UNIQUE, type TEXT,
  entity_type TEXT, entity_id TEXT, occurred_at TEXT, received_at TEXT, source TEXT, payload TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS evaluations (assignment_id TEXT PRIMARY KEY, at TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, assignment_id TEXT, status TEXT, severity TEXT, data TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS plans (plan_id TEXT PRIMARY KEY, incident_id TEXT, status TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS approvals (token TEXT PRIMARY KEY, plan_id TEXT, incident_id TEXT, actor TEXT, issued_at TEXT, expires_at TEXT, used_at TEXT);
CREATE TABLE IF NOT EXISTS executions (idempotency_key TEXT PRIMARY KEY, plan_id TEXT, at TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT, recipient TEXT, body TEXT, status TEXT,
  incident_id TEXT, requires_approval INTEGER, idempotency_key TEXT UNIQUE, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, sim_ts TEXT, kind TEXT, ref TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _dumps(obj: Any) -> str:
    return json.dumps(obj, default=_default, sort_keys=True)


def _default(o: Any):
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if hasattr(o, "isoformat"):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o)}")


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.audit_sink = None  # optional callable(record) for JSONL mirroring

    # ---- lifecycle -------------------------------------------------------
    def reset(self) -> None:
        with self.lock:
            for t in ("entities", "events", "evaluations", "incidents", "plans", "approvals", "executions", "outbox", "audit", "meta"):
                self.conn.execute(f"DROP TABLE IF EXISTS {t}")
            self.conn.executescript(SCHEMA)

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    # ---- entities ----------------------------------------------------------
    def get(self, kind: str, id: str) -> dict | None:
        rows = self._q("SELECT data FROM entities WHERE kind=? AND id=?", (kind, id))
        return json.loads(rows[0]["data"]) if rows else None

    def put(self, kind: str, id: str, data: Any) -> None:
        if hasattr(data, "model_dump"):
            data = data.model_dump(mode="json")
        self._x("INSERT OR REPLACE INTO entities VALUES (?,?,?,?)", (kind, id, _dumps(data), _now()))

    def all(self, kind: str) -> list[dict]:
        return [json.loads(r["data"]) for r in self._q("SELECT data FROM entities WHERE kind=? ORDER BY id", (kind,))]

    # ---- meta --------------------------------------------------------------
    def meta_get(self, key: str, default: Any = None) -> Any:
        rows = self._q("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def meta_set(self, key: str, value: Any) -> None:
        self._x("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, _dumps(value)))

    def next_seq(self, key: str, start: int = 0) -> int:
        with self.lock:
            n = int(self.meta_get(key, start)) + 1
            self.meta_set(key, n)
            return n

    # ---- events ------------------------------------------------------------
    def insert_event(self, ev: dict) -> bool:
        try:
            self._x(
                "INSERT INTO events (event_id, idempotency_key, type, entity_type, entity_id, occurred_at, received_at, source, payload) VALUES (?,?,?,?,?,?,?,?,?)",
                (ev["event_id"], ev["idempotency_key"], ev["type"], ev["entity_type"], ev["entity_id"],
                 ev["occurred_at"], ev["received_at"], ev["source"], _dumps(ev.get("payload", {}))),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def event_by_key(self, key: str) -> dict | None:
        rows = self._q("SELECT * FROM events WHERE idempotency_key=?", (key,))
        return _event_row(rows[0]) if rows else None

    def set_event_result(self, event_id: str, result: Any) -> None:
        self._x("UPDATE events SET result=? WHERE event_id=?", (_dumps(result), event_id))

    def list_events(self, limit: int = 50, include_telemetry: bool = True) -> list[dict]:
        where = "" if include_telemetry else "WHERE type NOT IN ('ELD_UPDATED','GPS_UPDATED','REFORECAST')"
        return [_event_row(r) for r in self._q(f"SELECT * FROM events {where} ORDER BY seq DESC LIMIT ?", (limit,))]

    def all_events(self) -> list[dict]:
        return [_event_row(r) for r in self._q("SELECT * FROM events ORDER BY seq")]

    # ---- evaluations ---------------------------------------------------------
    def put_evaluation(self, assignment_id: str, at: str, data: dict) -> None:
        self._x("INSERT OR REPLACE INTO evaluations VALUES (?,?,?)", (assignment_id, at, _dumps(data)))

    def get_evaluation(self, assignment_id: str) -> dict | None:
        rows = self._q("SELECT data FROM evaluations WHERE assignment_id=?", (assignment_id,))
        return json.loads(rows[0]["data"]) if rows else None

    # ---- incidents / plans ---------------------------------------------------
    def put_incident(self, inc: dict) -> None:
        self._x(
            "INSERT OR REPLACE INTO incidents VALUES (?,?,?,?,?,?,?)",
            (inc["id"], inc["assignment_id"], inc["status"], inc["severity"], _dumps(inc), inc["created_at"], inc["updated_at"]),
        )

    def get_incident(self, id: str) -> dict | None:
        rows = self._q("SELECT data FROM incidents WHERE id=?", (id,))
        return json.loads(rows[0]["data"]) if rows else None

    def list_incidents(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._q("SELECT data FROM incidents WHERE status=? ORDER BY id DESC", (status,))
        else:
            rows = self._q("SELECT data FROM incidents ORDER BY id DESC")
        return [json.loads(r["data"]) for r in rows]

    def open_incident_for(self, assignment_id: str) -> dict | None:
        rows = self._q("SELECT data FROM incidents WHERE assignment_id=? AND status='OPEN' ORDER BY id DESC LIMIT 1", (assignment_id,))
        return json.loads(rows[0]["data"]) if rows else None

    def put_plan(self, plan: dict, status: str = "PROPOSED") -> None:
        self._x("INSERT OR REPLACE INTO plans VALUES (?,?,?,?)", (plan["plan_id"], plan["incident_id"], status, _dumps(plan)))

    def get_plan(self, plan_id: str) -> tuple[dict, str] | None:
        rows = self._q("SELECT data, status FROM plans WHERE plan_id=?", (plan_id,))
        return (json.loads(rows[0]["data"]), rows[0]["status"]) if rows else None

    def set_plan_status(self, plan_id: str, status: str) -> None:
        self._x("UPDATE plans SET status=? WHERE plan_id=?", (status, plan_id))

    # ---- approvals / executions -------------------------------------------------
    def put_approval(self, token: str, plan_id: str, incident_id: str, actor: str, issued_at: str, expires_at: str) -> None:
        self._x("INSERT INTO approvals VALUES (?,?,?,?,?,?,NULL)", (token, plan_id, incident_id, actor, issued_at, expires_at))

    def get_approval(self, token: str) -> dict | None:
        rows = self._q("SELECT * FROM approvals WHERE token=?", (token,))
        return dict(rows[0]) if rows else None

    def use_approval(self, token: str, at: str) -> None:
        self._x("UPDATE approvals SET used_at=? WHERE token=?", (at, token))

    def get_execution(self, key: str) -> dict | None:
        rows = self._q("SELECT result FROM executions WHERE idempotency_key=?", (key,))
        return json.loads(rows[0]["result"]) if rows else None

    def put_execution(self, key: str, plan_id: str, at: str, result: dict) -> None:
        self._x("INSERT INTO executions VALUES (?,?,?,?)", (key, plan_id, at, _dumps(result)))

    # ---- outbox ------------------------------------------------------------------
    def outbox_add(self, channel: str, recipient: str, body: str, status: str, incident_id: str | None,
                   requires_approval: bool, idempotency_key: str, created_at: str) -> int | None:
        try:
            cur = self._x(
                "INSERT INTO outbox (channel, recipient, body, status, incident_id, requires_approval, idempotency_key, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (channel, recipient, body, status, incident_id, int(requires_approval), idempotency_key, created_at),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None

    def outbox_list(self, limit: int = 50, status: str | None = None) -> list[dict]:
        if status:
            rows = self._q("SELECT * FROM outbox WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))
        else:
            rows = self._q("SELECT * FROM outbox ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def outbox_set_status(self, id: int, status: str) -> None:
        self._x("UPDATE outbox SET status=? WHERE id=?", (status, id))

    # ---- audit -------------------------------------------------------------------
    def audit(self, kind: str, ref: str, data: Any, sim_ts: str | None = None) -> None:
        rec = {"ts": _now(), "sim_ts": sim_ts, "kind": kind, "ref": ref, "data": data}
        self._x("INSERT INTO audit (ts, sim_ts, kind, ref, data) VALUES (?,?,?,?,?)", (rec["ts"], sim_ts, kind, ref, _dumps(data)))
        if self.audit_sink:
            self.audit_sink(json.loads(_dumps(rec)))

    def audit_recent(self, limit: int = 100) -> list[dict]:
        rows = self._q("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def audit_for(self, ref_prefix: str) -> list[dict]:
        rows = self._q("SELECT * FROM audit WHERE ref LIKE ? ORDER BY seq", (ref_prefix + "%",))
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def _event_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
    d["result"] = json.loads(d["result"]) if d.get("result") else None
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
