"""Core-engine client used by the Slack adapter.

Two implementations behind one interface:
  - FakeCore: in-memory, backed by fixtures. Lets the Slack layer run and
    demo with zero core code.
  - HTTPCore (later): thin requests wrapper over the FastAPI the core team
    is building on the GB10. Swapping is one line in bot.py.

Mutating calls require an approval token and idempotency key (spec sec. 12).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core import fixtures
from app.core.models import ExecutionResult, Incident, IncidentStatus


class CoreClient:
    """Interface the Slack adapter codes against."""

    def get_incident(self, incident_id: str) -> Incident | None:
        raise NotImplementedError

    def get_driver_status(self, driver_id: str) -> dict | None:
        raise NotImplementedError

    def get_decision_trace(self, incident_id: str) -> list[str]:
        raise NotImplementedError

    def execute_approved_plan(
        self, plan_id: str, approval_token: str, idempotency_key: str
    ) -> ExecutionResult:
        raise NotImplementedError

    def record_override(self, incident_id: str, actor: str, reason: str) -> None:
        raise NotImplementedError


class FakeCore(CoreClient):
    def __init__(self) -> None:
        self.incidents: dict[str, Incident] = {}
        self._executed: dict[str, ExecutionResult] = {}  # idempotency_key -> result

    # --- demo helpers -------------------------------------------------
    def open_demo_incident(self) -> Incident:
        incident = fixtures.demo_incident()
        self.incidents[incident.incident_id] = incident
        return incident

    # --- reads ---------------------------------------------------------
    def get_incident(self, incident_id: str) -> Incident | None:
        return self.incidents.get(incident_id)

    def get_driver_status(self, driver_id: str) -> dict | None:
        return fixtures.DRIVERS.get(driver_id.upper())

    def get_decision_trace(self, incident_id: str) -> list[str]:
        incident = self.incidents.get(incident_id)
        if not incident:
            return []
        return [
            f"{incident.created_at.isoformat()} PICKUP_DELAYED +75min on load {incident.load_id}",
            "forecast_active_load -> delivery misses window by 58 min",
            f"check_hos_and_qualifications -> {incident.compliance.verdict.value}: "
            + ", ".join(f.code for f in incident.compliance.hard_failures),
            f"find_costed_alternatives -> {len(incident.plans)} compliant plans",
            f"recommend {incident.recommended_plan_id} (lowest cost, on-time, reserve "
            f"{incident.recommended.hos_reserve_minutes} min)",
        ]

    # --- writes (approval-gated) ----------------------------------------
    def execute_approved_plan(
        self, plan_id: str, approval_token: str, idempotency_key: str
    ) -> ExecutionResult:
        if idempotency_key in self._executed:
            return self._executed[idempotency_key]

        incident = next(
            (i for i in self.incidents.values() if i.plan(plan_id)), None
        )
        now = datetime.now(timezone.utc)
        if incident is None:
            result = ExecutionResult(ok=False, message=f"Unknown plan {plan_id}")
        elif approval_token != incident.approval_token:
            result = ExecutionResult(ok=False, message="Invalid approval token")
        elif incident.status != IncidentStatus.OPEN:
            result = ExecutionResult(
                ok=False, message=f"Incident already {incident.status.value}"
            )
        elif now > incident.expires_at:
            incident.status = IncidentStatus.EXPIRED
            result = ExecutionResult(
                ok=False,
                message="Recommendation expired; core must reforecast before execution.",
            )
        else:
            # Spec sec. 8: recheck compliance immediately before execution.
            plan = incident.plan(plan_id)
            assert plan is not None
            if plan.compliance_verdict.value != "PASS":
                result = ExecutionResult(
                    ok=False, message="Plan failed pre-execution compliance recheck"
                )
            else:
                incident.status = IncidentStatus.APPROVED
                result = ExecutionResult(
                    ok=True,
                    message=f"Executed {plan.title} for load {incident.load_id}",
                    executed_at=now,
                )

        self._executed[idempotency_key] = result
        return result

    def record_override(self, incident_id: str, actor: str, reason: str) -> None:
        incident = self.incidents.get(incident_id)
        if incident:
            incident.status = IncidentStatus.REJECTED
