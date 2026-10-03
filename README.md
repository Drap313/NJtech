# Dispatch Guardian v0.1

Pre-dispatch safety gate and always-on incident/approval surface for fuel &
oil trucking fleets. Deterministic code decides compliance; the local model
explains and coordinates; a human approves every change.

## Core engine (built)

- `app/models/entities.py` -- Driver, Vehicle, Trailer, Load, CustomerTerms, Assignment dataclasses
- `app/routing.py` -- toy distance table between named locations (swap for a real routing API later)
- `app/compliance/hos.py` -- deterministic HOS + qualification checks (11h/14h/break/reset/cycle/CDL/endorsements/equipment). Never guesses: unsupported exceptions and stale ELD data return "manual_review".
- `app/feasibility/assignment.py` -- `evaluate_assignment()`: wraps compliance with routing math (pickup/delivery windows, HOS reserve)
- `app/policies/schedule_risk.py` -- `score_schedule_risk()`: explainable fatigue-pattern scoring (never a medical claim)
- `app/optimization/costs.py` -- configurable cost model, `incremental_cost()`
- `app/optimization/alternatives.py` -- `find_costed_alternatives()`: the enumerative recovery solver (driver swap, relay, hold-for-reset, delivery-window ask, leave-unassigned), ranked
- `app/fleet_state.py` -- loads `data/*.json` into an in-memory FleetState
- `data/*.json` -- synthetic fleet: 10 drivers, 10 vehicles, 10 trailers, 3 customers, 9 loads (6 active + 3 proposed), matching the edge cases called for in the spec

```bash
python3 scripts/smoke_test.py   # core engine sanity check, no deps needed
```

## Slack adapter (built, on this branch)

Socket Mode bot: posts incidents with ranked priced recovery options and
approval buttons; approvals go through an approval-gated, idempotent,
expiring execute path with an append-only JSONL audit trail
(`audit/audit.jsonl`). Currently runs against `FakeCore`
(`app/core/client.py`); next step is a `RealCore` that calls the engine
above directly.

### One-time Slack app setup (browser, ~3 min)

1. https://api.slack.com/apps → **Create New App** → **From a manifest** →
   pick the workspace → paste `slack_app_manifest.yaml` from this repo.
2. **Basic Information → App-Level Tokens → Generate** with scope
   `connections:write` → copy the `xapp-` token.
3. **Install App → Install to Workspace** → copy the `xoxb-` bot token.
4. **Basic Information → Collaborators** → add teammates.
5. In Slack: create `#dispatch` and `/invite @dispatch-guardian`.

### Run the bot

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # paste the two tokens
.venv/bin/python -m app.adapters.slack.bot
```

Then in Slack:

- Type `demo` in a channel with the bot → incident **DG-I-0042** posts with
  three priced recovery options and approval buttons.
- Click **✅ Approve** → plan executes, the message updates with who
  approved, and an audit line lands in `audit/audit.jsonl`.
- `@dispatch-guardian status of D-03` → driver clocks and availability.
- `@dispatch-guardian why was DG-I-0042 flagged?` → the decision trace.

No tokens yet? Preview the incident message offline:

```bash
.venv/bin/python scripts/preview_blocks.py
# paste output into https://app.slack.com/block-kit-builder
```

## NOT built yet

- `app/monitoring/` -- event types + `process_event()` (the always-on loop)
- `app/api/` -- FastAPI endpoints
- `tests/` -- no automated tests yet, only the smoke checks
- `RealCore`: wiring the Slack bot's `CoreClient` to the real engine
- The OpenClaw/NemoClaw tool-calling layer (local planner model)

## Known simplifications (documented on purpose, not bugs)

- Driver "remaining minutes" fields are a current snapshot, not recomputed from duty_history.
- Routing is a hardcoded distance table (`app/routing.py`), not a real map.
- Relay points are hardcoded per route pair.
- Cost model rates in `app/optimization/costs.py` are placeholder defaults -- tune before trusting any dollar figure.

## Design rules carried from the spec

- Deterministic code decides compliance; the model only explains it.
- Nothing mutates state without dispatcher approval + audit entry.
- Every mutating call carries an approval token and idempotency key.
- Recommendations expire; expired approvals are refused and reforecast.
