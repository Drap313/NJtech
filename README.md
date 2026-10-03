# Dispatch Guardian v0.1 -- core engine (partial build)

## What's built
- `app/models/entities.py` -- Driver, Vehicle, Trailer, Load, CustomerTerms, Assignment dataclasses
- `app/routing.py` -- toy distance table between named locations (swap for a real routing API later)
- `app/compliance/hos.py` -- deterministic HOS + qualification checks (11h/14h/break/reset/cycle/CDL/endorsements/equipment). Never guesses: unsupported exceptions and stale ELD data return "manual_review".
- `app/feasibility/assignment.py` -- `evaluate_assignment()`: wraps compliance with routing math (pickup/delivery windows, HOS reserve)
- `app/policies/schedule_risk.py` -- `score_schedule_risk()`: explainable fatigue-pattern scoring (never a medical claim)
- `app/optimization/costs.py` -- configurable cost model, `incremental_cost()`
- `app/optimization/alternatives.py` -- `find_costed_alternatives()`: the enumerative recovery solver (driver swap, relay, hold-for-reset, delivery-window ask, leave-unassigned), ranked
- `app/fleet_state.py` -- loads `data/*.json` into an in-memory FleetState
- `data/*.json` -- synthetic fleet: 10 drivers, 10 vehicles, 10 trailers, 3 customers, 9 loads (6 active + 3 proposed), matching the edge cases called for in the spec (approaching reset, consecutive overnights, stale ELD, driver reporting fatigue, wrong CDL class, etc.)

## NOT built yet
- `app/monitoring/` -- event types + `process_event()` (the always-on loop)
- `app/execution/` -- approvals + audit trail
- `app/api/` -- FastAPI endpoints
- `tests/` -- no automated tests yet, only the smoke checks below
- The OpenClaw tool-calling layer that lets the Slack agent actually call these functions

## Quickstart
```bash
python3 -m venv .venv && source .venv/bin/activate   # optional but recommended
pip install -r requirements.txt                       # only needed for API/tests, core engine has no deps

python3 scripts/smoke_test.py
```

## Known simplifications (documented on purpose, not bugs)
- Driver "remaining minutes" fields are a current snapshot, not recomputed from duty_history.
- Routing is a hardcoded distance table (`app/routing.py`), not a real map.
- Relay points are hardcoded per route pair.
- Cost model rates in `app/optimization/costs.py` are placeholder defaults -- tune before trusting any dollar figure.
