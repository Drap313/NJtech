"""CLI: print the fleet, replay scenarios deterministically, inspect incidents.

  python -m app.cli fleet
  python -m app.cli replay primary [--approve] [--explain]
  python -m app.cli incidents
  python -m app.cli evaluate A-204
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.table import Table

from .clock import SimClock, minutes_between, utc
from .config import RUNTIME_DIR, SYNTHETIC_DIR, get_rules, load_yaml
from .db import Store
from .events.processor import Engine, make_event
from .forecasting.evaluator import evaluate_assignment
from .incidents import service
from .seed import seed
from .state import Fleet

ET = ZoneInfo("America/New_York")
con = Console()


def hm(iso: str | None) -> str:
    return datetime.fromisoformat(iso).astimezone(ET).strftime("%a %H:%M") if iso else "-"


def open_store(path: str | None) -> Store:
    return Store(path or RUNTIME_DIR / "cli.db")


def print_fleet(store: Store, clock: SimClock) -> None:
    now = clock.now()
    fleet = Fleet.load(store)
    R = get_rules()
    con.print(f"[bold]Fleet at {now.astimezone(ET):%a %Y-%m-%d %H:%M %Z}[/bold]")
    t = Table("Driver", "Name", "Avail", "Duty", "Location", "ELD age", "Drive/Shift/Cycle used", "Endorse", "Tractor")
    for d in sorted(fleet.drivers.values(), key=lambda d: d.id):
        age = minutes_between(utc(d.eld.observed_at), now)
        loc = fleet.net.loc(d.position.location_id).name if d.position.location_id else f"{d.position.lat:.2f},{d.position.lon:.2f}"
        t.add_row(d.id, d.name, d.availability, d.eld.duty_status.value, loc,
                  f"[red]{age:.0f}m STALE[/red]" if age > R.max_eld_age else f"{age:.0f}m",
                  f"{d.eld.drive_minutes_in_shift}m / {hm(d.eld.shift_start.isoformat()) if d.eld.shift_start else '-'} / {d.eld.cycle_used_minutes / 60:.1f}h",
                  ",".join(d.endorsements) or "-", d.tractor_id or "-")
    con.print(t)
    t = Table("Assignment", "Load", "Status", "Driver", "Stage", "Verdict", "Delivery ETA", "Slack", "Reserve", "Risk")
    for a in sorted(fleet.assignments.values(), key=lambda a: a.id):
        ev = store.get_evaluation(a.id) or {}
        v = ev.get("verdict", "-")
        color = {"PASS": "green", "FAIL": "red", "UNKNOWN": "yellow", "MANUAL_REVIEW": "yellow"}.get(v, "white")
        t.add_row(a.id, a.load_id, a.status, a.driver_id or "-", a.progress.stage, f"[{color}]{v}[/{color}]", hm(ev.get("delivery_eta")),
                  str(ev.get("appointment_slack_minutes", "-")), str(ev.get("hos_reserve_minutes", "-")),
                  str((ev.get("risk") or {}).get("score", "-")))
    con.print(t)
    t = Table("Tractor", "Status", "Maintenance")
    for tr in sorted(fleet.tractors.values(), key=lambda x: x.id):
        t.add_row(tr.id, tr.status, "; ".join(f"{hm(b.start.isoformat())}-{hm(b.end.isoformat())} {b.reason}" for b in tr.maintenance) or "-")
    con.print(t)


def replay(store: Store, scenario: str, approve: bool = False, explain: bool = False, quiet: bool = False) -> dict:
    clock = SimClock(store)
    engine = Engine(store, clock)
    start = seed(store)
    clock.set(start, paused=True)
    engine.sweep("SEED")
    raw = load_yaml(SYNTHETIC_DIR / "scenarios.yaml")[scenario]
    opened = []
    for i, e in enumerate(raw["events"]):
        at = datetime.fromisoformat(e["at"])
        clock.set(at, paused=True)
        ev = make_event(e["type"], e["entity_type"], e["entity_id"], e.get("payload", {}), e["source"], at, at, f"replay:{scenario}:{i}")
        res = engine.process_event(ev)
        if not quiet:
            con.print(f"[cyan]{hm(at.isoformat())}[/cyan] {e['type']} {e['entity_id']} -> processed in {res.get('processing_ms')} ms")
        for a in res["affected"]:
            inc = a.get("incident") or {}
            if inc.get("action") in ("opened", "updated"):
                opened.append(inc["incident_id"])
    out = {"incidents": opened}
    for iid in opened:
        inc = store.get_incident(iid)
        if not quiet:
            con.print()
            con.rule(f"{iid}")
            con.print(inc["summary"])
            if inc.get("rejected"):
                con.print("\n[dim]Excluded:[/dim]")
                for r in inc["rejected"][:6]:
                    con.print(f"[dim]  x {r['title']}: {', '.join(r['codes'])}[/dim]")
        if approve and inc.get("recommended_plan_id"):
            pid = inc["recommended_plan_id"]
            tok = service.approve_plan(store, clock, pid, "dispatcher:demo")
            res = service.execute_approved_plan(store, clock, pid, tok["approval_token"], f"demo:{pid}")
            out["execution"] = res
            if not quiet:
                con.print(f"\n[green]Approved by dispatcher:demo and executed {pid}[/green]")
                for ch in res["changes"]:
                    con.print(f"  - {ch}")
                con.print(f"  revalidated: {res['revalidation']['verdict']}, delivery {hm(res['revalidation']['delivery_eta'])}, "
                          f"slack {res['revalidation']['appointment_slack_minutes']} min")
        if explain:
            from .agent.llm import LocalLLM
            from .agent.runner import explain_incident
            from .agent.tools import ToolBox
            r = explain_incident(ToolBox(store, clock), LocalLLM(), iid)
            out["explanation"] = r
            if not quiet:
                con.print(f"\n[bold]Local model ({r['model']}, {r['elapsed_s']} s, tools: {[s['tool'] for s in r['steps']]}):[/bold]")
                con.print(r["answer"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="dispatch-guardian")
    ap.add_argument("--db", help="SQLite path (default data/runtime/cli.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fleet")
    r = sub.add_parser("replay")
    r.add_argument("scenario", nargs="?", default="primary")
    r.add_argument("--approve", action="store_true")
    r.add_argument("--explain", action="store_true")
    sub.add_parser("incidents")
    e = sub.add_parser("evaluate")
    e.add_argument("assignment_id")
    sub.add_parser("seed")
    args = ap.parse_args()
    store = open_store(args.db)
    clock = SimClock(store)
    if args.cmd == "seed" or store.meta_get("scenario_start") is None:
        start = seed(store)
        clock.set(start, paused=True)
        Engine(store, clock).sweep("SEED")
    if args.cmd == "fleet":
        print_fleet(store, clock)
    elif args.cmd == "replay":
        replay(store, args.scenario, args.approve, args.explain)
    elif args.cmd == "incidents":
        for i in store.list_incidents():
            con.print(f"{i['id']} {i['status']} {i['severity']} {i['title']}")
    elif args.cmd == "evaluate":
        ev = evaluate_assignment(Fleet.load(store), args.assignment_id, clock.now())
        ev.pop("timeline")
        ev["compliance"].pop("rule_trace")
        con.print_json(json.dumps(ev, default=str))


if __name__ == "__main__":
    main()
