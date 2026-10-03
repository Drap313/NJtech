# Dispatch Guardian

## Local build specification

**Version:** 0.1  
**Target:** Dell Pro Max with GB10; all inference local  
**Initial scope:** General U.S. trucking, with the first ruleset limited to standard interstate property-carrier hours of service

## 1. Product definition

Dispatch Guardian is an always-on dispatch operations agent. It continuously evaluates drivers, tractors, trailers, loads, routes, appointments, and qualifications. When a plan becomes illegal, infeasible, or operationally fragile, it opens an incident and proposes the lowest-cost recovery options.

It is not only a pre-dispatch checker. Its defining capability is reacting after dispatch when reality changes: a shipper delay, driver rejection, stale ELD feed, equipment failure, appointment change, or worsening ETA.

The system does not replace the dispatcher. Deterministic code decides compliance; the local model coordinates tools, compares plans, explains the situation, and drafts communications. A human approves assignment changes and external commitments.

### Product promise

- Stop an invalid or unqualified assignment before release.
- Detect when a previously valid plan is breaking.
- Explain the exact constraint or rule that failed.
- Produce executable alternatives with ETA, cost, legal reserve, and risk.
- Keep driver, rate, customer, and operational data on the GB10.
- Continue monitoring even when nobody is actively chatting with the agent.

## 2. The first convincing end-to-end scenario

1. A load is assigned to a legal, qualified driver.
2. A pickup delay adds 75 minutes to the plan.
3. Dispatch Guardian automatically reevaluates the active assignment.
4. It finds that the driver will exceed the duty window and miss delivery.
5. It rejects “continue unchanged” and generates three compliant alternatives.
6. Each alternative includes arrival time, cost delta, legal reserve, assumptions, and customer impact.
7. It recommends the best plan and asks the dispatcher for approval.
8. After approval, it updates the synthetic dispatch state and records the full decision trace.

This scenario proves the product is always on, agentic, operationally useful, and more than a chat wrapper.

## 3. Feature priorities

| Priority | Capability | MVP evidence |
|---|---|---|
| P0 | Canonical fleet state | Current drivers, equipment, loads, and assignments can be queried consistently. |
| P0 | Deterministic compliance engine | Boundary tests pass and every verdict includes a rule trace. |
| P0 | Assignment feasibility | ETA, appointment slack, and legal reserve change after an event. |
| P0 | Costed recovery solver | At least three alternatives are ranked with cost breakdowns. |
| P0 | Event-driven reevaluation | A delay automatically opens or updates an incident. |
| P1 | Schedule-risk policy | Every risk point has a visible factor and evidence. |
| P1 | Approval and audit workflow | Approve, reject, override, and reconstruct a decision. |
| P2 | Voice and document intake | One voice update and one printed log can become structured events. |
| Later | Slack/OpenClaw interface | Added after the core API and event loop are stable. |

### Explicit non-goals for the first build

- Replacing a TMS, ELD provider, telematics platform, or payroll system.
- Diagnosing fatigue or claiming a driver is medically unsafe.
- Supporting every federal exception, state rule, and specialized fleet on day one.
- Automatically assigning drivers or changing appointments without approval.
- Using an LLM for legal arithmetic.
- Training a foundation model.

## 4. Canonical data model

Every record needs a source, observed timestamp, and freshness limit. Stale data must produce `UNKNOWN` or `MANUAL_REVIEW`, never a confident pass.

### Driver

- ID and home terminal
- Current location
- Duty status and status-start time
- Driving, shift, and cycle clocks
- Cycle profile
- CDL class, endorsements, and restrictions
- Availability
- Data source and timestamp

Derived values include drive time remaining, duty-window time remaining, cycle time remaining, earliest reset, qualification match, and freshness.

### Tractor and trailer

- ID, location, and operating status
- Equipment type and capacity
- Restrictions and compatibility
- Maintenance or availability blocks
- Data source and timestamp

### Load

- Origin and destination
- Pickup and delivery windows
- Expected loading and unloading time
- Planned miles and route duration
- Revenue and service-failure terms
- Cargo, equipment, and qualification requirements
- Priority, customer, and current status

### Assignment

- Load, driver, tractor, and trailer
- Planned legs and stops
- Approval and execution status
- Forecasted ETA, appointment slack, and legal reserve
- Compliance verdict and projected cost

### Event

```text
Event {
  event_id,
  type,
  entity_type,
  entity_id,
  occurred_at,
  received_at,
  source,
  payload,
  idempotency_key
}
```

## 5. Deterministic compliance engine

Implement legal and company constraints as versioned Python code plus configuration. The model may call the tool and explain its output, but it cannot calculate legality or override a hard failure.

The first ruleset should cover standard U.S. interstate property-carrier rules, including:

- Driving-limit projection
- On-duty-window projection
- Required break calculation
- Configurable 60-hour/7-day and 70-hour/8-day cycles
- Qualifying reset and earliest-next-available time
- CDL class and endorsement matching
- Equipment and driver restrictions
- Company policies that are stricter than the legal baseline

Verify the final rule implementation against authoritative regulations before production use. Unsupported exceptions should return `MANUAL_REVIEW`.

### Compliance result

```json
{
  "verdict": "PASS | FAIL | UNKNOWN | MANUAL_REVIEW",
  "hard_failures": [
    {"code": "SHIFT_LIMIT", "excess_minutes": 41}
  ],
  "warnings": [
    {"code": "LOW_RESERVE", "remaining_minutes": 18}
  ],
  "projected_clocks": {
    "drive_left_minutes": 0,
    "shift_left_minutes": -41
  },
  "ruleset": "us_federal_property_v1",
  "rule_trace": [],
  "input_freshness": {}
}
```

### Required boundary tests

- Exactly at each configured limit
- One minute beyond each limit
- Break becomes due during a planned leg
- Driver lacks one required endorsement
- ELD snapshot is stale
- Company policy is stricter than the legal baseline
- Time-zone transition crosses an appointment boundary

## 6. Feasibility evaluator

A legal assignment can still be operationally impossible. Forecast every plan as a timeline:

1. Reposition to origin
2. Check-in and loading
3. Transit
4. Required stops and breaks
5. Delivery and unloading
6. Post-delivery commitments

The output should identify the first point of failure and return:

- Pickup and delivery ETA
- Appointment slack
- Driving and duty-window reserve
- Required stops
- Data freshness
- Hard failures and warnings

Account for driver, tractor, and trailer locations independently. Use route duration plus configurable operating buffers rather than distance alone.

## 7. Cost model

Every coefficient should be visible and configurable. Separate contractual facts from estimated costs.

```text
incremental_cost =
    reposition_miles * cost_per_mile
  + driver_extra_minutes * labor_cost_per_minute
  + detention_minutes * detention_cost_per_minute
  + relay_handling_cost
  + equipment_swap_cost
  + estimated_service_failure_cost
  + optional_risk_penalty
```

Minimum cost components:

- Reposition and loaded miles
- Driver time
- Detention
- Relay handling
- Tractor or trailer swap
- Late-delivery or service-failure exposure
- Appointment change cost

## 8. Recovery solver

Generate a bounded candidate set, eliminate every hard-constraint failure, and rank the survivors. A deterministic enumerator is enough for the first single-incident demo; use OR-Tools when multiple loads and drivers interact.

Candidate actions:

- Swap to another qualified driver
- Relay at a feasible interchange point
- Hold for a qualifying reset
- Swap tractor or trailer
- Request a new appointment
- Rebalance a lower-priority load to free a resource

Rank in this order:

1. Hard compliance
2. Service feasibility
3. Expected incremental cost
4. Schedule risk
5. Operational complexity

```text
RecoveryPlan {
  plan_id,
  incident_id,
  actions[],
  required_approvals[],
  compliance_verdict,
  pickup_eta,
  delivery_eta,
  appointment_slack_minutes,
  hos_reserve_minutes,
  incremental_cost,
  cost_breakdown[],
  risk_score,
  assumptions[],
  expires_at
}
```

Every returned plan must be independently rechecked by the same compliance evaluator before presentation and again immediately before execution.

## 9. Schedule-risk policy

Call this **schedule risk**, not fatigue detection. It is a transparent operational heuristic based on work patterns and plan fragility. It must not claim that a driver is tired or unfit.

Illustrative factors:

| Factor | Example points |
|---|---:|
| Repeated overnight duty periods | +20 |
| Short turnaround between duty periods | +20 |
| Projected HOS reserve under 30 minutes | +30 |
| Driving near the end of the duty window | +15 |
| Recent assignment or schedule change | +10 |
| Fresh driver confirmation and ample reserve | -10 |

Suggested bands: 0–29 normal, 30–59 elevated, 60–79 high, 80–100 critical. The score may trigger review or change ranking, but it cannot declare a driver medically fatigued.

## 10. Always-on event loop

### Initial event types

- `LOAD_CREATED`
- `ASSIGNMENT_PROPOSED`
- `ASSIGNMENT_APPROVED`
- `DRIVER_ACCEPTED`
- `DRIVER_DECLINED`
- `DRIVER_STATUS_CHANGED`
- `DRIVER_DELAY_REPORTED`
- `ELD_UPDATED`
- `GPS_UPDATED`
- `PICKUP_STARTED`
- `PICKUP_DELAYED`
- `PICKUP_COMPLETED`
- `DELIVERY_WINDOW_CHANGED`
- `VEHICLE_UNAVAILABLE`
- `TRAILER_UNAVAILABLE`
- `ROUTE_TIME_CHANGED`
- `CUSTOMER_MESSAGE_RECEIVED`
- `DATA_BECAME_STALE`

### Processing loop

```python
def process_event(event):
    validate_and_store(event)
    affected = resolve_affected_assignments(event)

    for assignment in affected:
        forecast = forecast_active_load(assignment)
        compliance = check_hos_and_qualifications(assignment, forecast)
        risk = score_schedule_risk(assignment, forecast)

        if material_change_or_threshold_crossed(forecast, compliance, risk):
            options = find_costed_alternatives(assignment, forecast)
            incident = create_or_update_incident(
                assignment, forecast, compliance, risk, options
            )
            notify_or_request_approval(incident)

    append_decision_trace(event)
```

### Cadence

- Process pushed events immediately.
- Reforecast active demo loads every 60 seconds.
- Run a rolling fleet optimization every 10 minutes or after a high-severity event.
- Check stale critical feeds every 60 seconds.
- Deduplicate incidents and suppress repeated alerts unless severity, ETA, cost, or recommendation changes materially.

## 11. Autonomy boundaries

### May happen automatically

- Ingest and normalize events
- Run compliance, forecast, risk, and optimization tools
- Open, merge, update, and close internal incidents
- Draft internal and customer messages
- Request missing status information

### Requires human approval

- Assign or swap a driver
- Commit a relay
- Swap equipment
- Change a customer appointment
- Send an external commitment
- Release a load after an override

### Prohibited

- Altering ELD records
- Bypassing a hard compliance failure
- Claiming a driver is medically fatigued
- Hiding uncertainty or stale inputs
- Executing irreversible external actions without authorization

## 12. Agent tools

Expose narrow typed tools. Tools own facts, arithmetic, optimization, and writes.

```text
get_fleet_state()
get_driver_status(driver_id)
get_load_status(load_id)
check_hos_and_qualifications(assignment, forecast)
evaluate_assignment(assignment_id, at_time)
score_schedule_risk(driver_id, proposed_plan)
forecast_active_load(assignment_id)
find_costed_alternatives(incident_id, max_options=5)
create_dispatch_incident(payload)
request_driver_confirmation(driver_id, question)
draft_customer_message(incident_id, plan_id)
execute_approved_plan(plan_id, approval_token, idempotency_key)
record_override(incident_id, actor, reason)
get_decision_trace(incident_id)
```

Every mutating tool must require an approval token and idempotency key.

## 13. Local GB10 architecture

The GB10 is properly used when it hosts the reasoning, retrieval, multimodal intake, and orchestration locally. Do not waste the model on arithmetic that deterministic code can perform better.

| Layer | First implementation |
|---|---|
| Local inference | One Nemotron- or Qwen-class planner served locally |
| Agent loop | NemoClaw/OpenClaw or a minimal typed tool-calling loop |
| API | FastAPI with typed schemas |
| Rules | Plain Python with versioned YAML/JSON policies |
| Optimization | Deterministic enumeration, then OR-Tools |
| Database | SQLite for the prototype; PostgreSQL later |
| Events | Async queue plus replayable event table |
| Retrieval | Local embeddings with FAISS or SQLite vector search |
| Security | OpenShell egress policy and local secrets |
| Audit | Append-only JSONL plus database records |

### Runtime separation

- **Hot path:** ingestion, rules, forecasts, and thresholds; must work without an LLM.
- **Reasoning path:** tool selection, plan comparison, explanation, and drafting.
- **Multimodal path:** speech transcription and document extraction when media arrives.
- **Write path:** approval-gated changes, with deterministic revalidation before commit.

### Suggested repository structure

```text
dispatch-guardian/
  app/
    api/
    agent/
    compliance/
    forecasting/
    optimization/
    events/
    incidents/
    adapters/
    audit/
  config/
    rulesets/
    cost_models/
  data/
    synthetic/
  tests/
    compliance/
    forecasting/
    optimization/
    agent_safety/
  scripts/
    seed_demo.py
    run_demo_scenario.py
  README.md
```

## 14. Synthetic demo fleet

Seed a deterministic local dataset with:

- 10 drivers with varied clocks, endorsements, terminals, and schedules
- 8 tractors and 10 trailers with several equipment types
- 6 active loads and 3 proposed loads
- 3 customers and 2 terminals
- One planned equipment outage
- Different detention and late-delivery terms
- Deterministic route durations

Edge cases should include a stale ELD feed, missing endorsement, driver rejection, pickup delay, equipment failure, and changed delivery window.

### Expected primary incident

```text
INCIDENT DG-I-0042 — HIGH

Load DG-204 is no longer feasible after a 75-minute pickup delay.
Cause: projected duty-window overrun of 41 minutes.
Customer impact: current plan misses delivery by 58 minutes.

Recommended: relay to D-03 at Exit 18
  Delivery: on time, with 22 minutes of slack
  Legal reserve: 64 minutes
  Incremental cost: $286

Alternatives:
  Swap to D-09 — on time, +$411
  Hold for reset — compliant, delivery +9h 35m,
  estimated service cost $1,200

Required action: dispatcher approval.
Recommendation expires in 12 minutes.
```

## 15. Build order

### Milestone 1: State and fixtures

Build typed models, the SQLite schema, the synthetic fleet, and event replay.

**Exit condition:** a CLI can print the current fleet and replay a deterministic event stream.

### Milestone 2: Compliance

Build HOS projection, qualification checks, versioned policies, and boundary tests.

**Exit condition:** all tests pass and no LLM participates in the verdict.

### Milestone 3: Forecast and cost

Build timeline projection, ETA, legal reserve, appointment slack, and configurable costs.

**Exit condition:** a delay predictably changes ETA, feasibility, and cost.

### Milestone 4: Recovery

Build candidate generation, hard-constraint filtering, and ranking.

**Exit condition:** the system returns three alternatives and excludes illegal plans.

### Milestone 5: Always-on loop

Build event consumption, incident lifecycle, deduplication, and audit traces.

**Exit condition:** a pickup delay automatically opens one incident.

### Milestone 6: Local agent

Connect the local model to the typed tools and approval gate.

**Exit condition:** the model can explain and recommend but cannot mutate state without approval.

### Milestone 7: Optional multimodal intake

Add voice transcription and printed-document extraction.

**Exit condition:** media becomes a structured event with provenance and confidence.

### Milestone 8: Slack and UI

Add OpenClaw, Slack controls, and a compact operations dashboard only after the core API works.

## 16. First four functions to implement

```python
evaluate_assignment(assignment_id, at_time) -> AssignmentEvaluation
score_schedule_risk(driver_id, proposed_plan) -> ScheduleRiskResult
find_costed_alternatives(incident_id, max_options=5) -> list[RecoveryPlan]
process_event(event) -> EventProcessingResult
```

Start with `evaluate_assignment`, its input records, and boundary tests. Everything else should consume that stable contract.

## 17. Definition of done for the core

- The primary scenario runs from one command and is deterministic.
- An operational event causes reevaluation within five seconds.
- Compliance decisions cite a versioned rule trace.
- Every proposed recovery independently passes the compliance evaluator.
- Every option includes ETA, cost delta, legal reserve, assumptions, and expiration.
- The entire scenario works with outbound network access disabled.
- No operational mutation occurs without approval and an audit entry.
- The decision can be reconstructed from persisted inputs, tool outputs, and policy versions.

## 18. Decisions to preserve

- Rules determine legality; the model never does.
- Monitoring continues after dispatch.
- Legal, feasible, economical, and low-risk are separate outputs.
- Unknown and stale data remain explicit.
- The local model handles unstructured intake, orchestration, explanation, and recovery reasoning.
- Human approval remains mandatory for assignments and external commitments.
- A narrow, testable first ruleset is stronger than broad, unreliable coverage.
