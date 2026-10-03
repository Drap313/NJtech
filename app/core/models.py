"""Typed contracts shared with the core engine (spec sections 5, 8, 14).

These models ARE the interface between the Slack adapter and the
compliance/forecast/recovery engine being built in parallel. Change them
only by agreement with the rules/data role.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class RuleFailure(BaseModel):
    code: str
    excess_minutes: int | None = None
    detail: str | None = None


class ComplianceResult(BaseModel):
    verdict: Verdict
    hard_failures: list[RuleFailure] = Field(default_factory=list)
    warnings: list[RuleFailure] = Field(default_factory=list)
    projected_clocks: dict[str, int] = Field(default_factory=dict)
    ruleset: str = "us_federal_property_v1"
    rule_trace: list[str] = Field(default_factory=list)
    input_freshness: dict[str, str] = Field(default_factory=dict)


class CostComponent(BaseModel):
    label: str
    amount_usd: float


class RecoveryPlan(BaseModel):
    plan_id: str
    incident_id: str
    title: str  # short human label, e.g. "Relay to D-03 at Exit 18"
    actions: list[str] = Field(default_factory=list)
    required_approvals: list[str] = Field(default_factory=list)
    compliance_verdict: Verdict = Verdict.PASS
    pickup_eta: datetime | None = None
    delivery_eta: datetime | None = None
    appointment_slack_minutes: int | None = None
    hos_reserve_minutes: int | None = None
    incremental_cost: float = 0.0
    cost_breakdown: list[CostComponent] = Field(default_factory=list)
    risk_score: int = 0
    assumptions: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class IncidentSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"


class Incident(BaseModel):
    incident_id: str
    severity: IncidentSeverity
    status: IncidentStatus = IncidentStatus.OPEN
    load_id: str
    summary: str  # "Load DG-204 is no longer feasible after a 75-minute pickup delay."
    cause: str  # "projected duty-window overrun of 41 minutes"
    customer_impact: str
    compliance: ComplianceResult
    recommended_plan_id: str
    plans: list[RecoveryPlan] = Field(default_factory=list)
    approval_token: str
    created_at: datetime
    expires_at: datetime
    # Slack bookkeeping: set once the incident message is posted
    slack_channel: str | None = None
    slack_ts: str | None = None

    def plan(self, plan_id: str) -> RecoveryPlan | None:
        return next((p for p in self.plans if p.plan_id == plan_id), None)

    @property
    def recommended(self) -> RecoveryPlan:
        found = self.plan(self.recommended_plan_id)
        if found is None:
            raise ValueError(f"recommended plan {self.recommended_plan_id} missing")
        return found


class ExecutionResult(BaseModel):
    ok: bool
    message: str
    executed_at: datetime | None = None
