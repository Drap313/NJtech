# NJtech: Dispatch Guardian

NVIDIA x Dell Hackathon Project. An always-on dispatch operations agent running fully on the Dell Pro Max GB10.
See [DISPATCH_GUARDIAN_BUILD_SPEC.md](DISPATCH_GUARDIAN_BUILD_SPEC.md) for the product spec.

Deterministic code decides legality, ETAs and costs. The local models handle explanation, tool orchestration and
unstructured intake. A human approves every assignment change.

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Dashboard + API + always-on monitor + mock fleet  ->  http://localhost:8090
.venv/bin/uvicorn app.api.main:app --host 127.0.0.1 --port 8090

# The primary scenario in one deterministic command (no server needed)
.venv/bin/python scripts/run_demo_scenario.py --approve --explain

# CLI
.venv/bin/python -m app.cli fleet                     # print the current fleet
.venv/bin/python -m app.cli replay primary            # replay a scripted event stream
.venv/bin/python -m app.cli replay equipment_failure  # also: driver_decline, window_change

# Tests (boundary tests, forecasting, recovery, event loop, agent safety)
.venv/bin/python -m pytest -q
```

Local services it uses (already running on the GB10):

| Service | Port | Used for |
|---|---|---|
| vLLM `nvidia/Qwen3.6-35B-A3B-NVFP4` | 8000 | agent tool loop, incident explanations, text intake (schema-constrained JSON) |
| Ollama `qwen3-vl:30b` | 11434 | printed document / photo intake |
| Dispatch Guardian | 8090 | API + dashboard |

Override with `DG_LLM_URL`, `DG_LLM_MODEL`, `DG_VLM_URL`, `DG_VLM_MODEL`. `DG_SPEED=60` starts the sim clock at 60x.
`DG_RUNTIME=0` disables the background monitor. The hot path (rules, forecasts, incidents) works with both models offline.

## Demo flow (dashboard)

The dashboard is a multi-page app: Overview, Incidents, Loads, Drivers, Live map, Assistant, Activity & audit, Policies & models.
From a laptop, tunnel in with `ssh -L 8090:localhost:8090 dell@<gb10-ip>` and open http://localhost:8090.

1. The fleet starts at Tue 06:30 ET. The monitor already flags **DG-I-0041**: D-05's ELD feed died at 03:10, so DG-205 is `UNKNOWN`, not a pass, and a status request is queued to the driver.
2. On **Overview → Simulate an event**, click *Pickup delay breaks DG-204*. This opens **DG-I-0042 (HIGH)**: the 75-minute delay pushes D-01 41 minutes past the 14-hour window.
   The incident page compares the options: relay to D-03 at Exit 18 (on time, 22 min slack, 64 min reserve, +$286), swap to D-09 (+$411, 7 min slack), hold for reset (+$1,200 late exposure).
   Excluded options show why (D-06 cycle hours, D-10 in restart, ...).
3. **Explain with local model** → Qwen3.6 explains the incident using the tool outputs.
4. **Approve & execute** → deterministic revalidation, relay committed, driver messages queued, full audit trail.
5. Set the clock to 60x or 300x and watch on the Live map: D-01 drives to Exit 18, D-03 leaves Newark just in time, the handoff completes, and the load delivers at 18:38.

Other scenarios: D-07 declines DG-208, T-04 breaks down at Harrisburg, Hudson Fresh pulls DG-203's window earlier.
On **Assistant**, paste a free-text message or upload a photo of a document (sample in `app/api/static/samples/`) and the local models turn it into a structured event.
Use **Policies & models → Reset demo fleet** to replay from 06:30.

## Layout

```
app/
  compliance/   hos.py (minute-accurate HOS projection), qualifications.py, engine.py (verdict + rule trace)
  forecasting/  planner.py (plans -> per-driver timelines), evaluator.py (evaluate_assignment), risk.py (schedule risk)
  optimization/ costs.py (configurable cost model), solver.py (find_costed_alternatives)
  events/       processor.py (process_event, hot path), runtime.py (always-on loop + mock fleet telemetry)
  incidents/    service.py (lifecycle, dedupe, approval gate, execution, overrides)
  agent/        llm.py (vLLM + Ollama clients), tools.py (typed tools + gate), runner.py (tool loop), intake.py
  api/          main.py (FastAPI), static/index.html (dashboard, no external assets)
  audit/        trail.py (append-only JSONL + DB, decision traces)
config/         rulesets/ (us_federal_property_v1, company_policy_v1), cost_models/, risk_policy_v1.yaml
data/synthetic/ fleet.yaml (10 drivers, 8 tractors, 10 trailers, 9 loads), network.yaml, scenarios.yaml
data/runtime/   SQLite DBs + audit.jsonl (gitignored)
```

## Integration points (for the Slack / OpenClaw work)

- `GET  /api/incidents?status=OPEN`: open incidents with ranked plans; `summary` is ready-to-post text
- `GET  /api/outbox?status=queued`: dispatcher notifications and driver messages waiting to be delivered
- `POST /api/plans/{plan_id}/approve {"actor": "slack:U123"}` → `approval_token`
- `POST /api/plans/{plan_id}/execute {"approval_token", "idempotency_key"}`
- `POST /api/plans/{plan_id}/reject {"actor", "reason"}`
- `POST /api/agent/chat {"message", "incident_id"?}`: local agent with read-only tools
- `POST /api/events`: push any event; `POST /api/intake/text`, `POST /api/intake/document` (multipart)
- `GET  /api/incidents/{id}/trace`: inputs, policy versions, tool outputs, approvals

The server binds to 127.0.0.1. To reach it from the OpenClaw sandbox, bind to the docker bridge
(`--host 0.0.0.0` or `172.18.0.1`) and allow that host:port in the OpenShell egress policy.

## Known limits of this base version

- HOS ruleset is the standard property-carrier baseline only; sleeper-berth splits, adverse conditions and short-haul return `MANUAL_REVIEW`.
  Daily 60/70-hour roll-off is not modeled (conservative; crossing midnight sends cycle overruns to manual review). Verify against the eCFR before production use.
- Solver is a bounded deterministic enumerator for one incident at a time (no OR-Tools fleet optimization yet; no "rebalance a lower-priority load" candidate).
- Voice intake and retrieval (embeddings) are not built yet.
