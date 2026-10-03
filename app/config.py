"""Paths, endpoints and versioned policy loading."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SYNTHETIC_DIR = ROOT / "data" / "synthetic"
RUNTIME_DIR = Path(os.environ.get("DG_RUNTIME_DIR", ROOT / "data" / "runtime"))
DB_PATH = Path(os.environ.get("DG_DB", RUNTIME_DIR / "dispatch.db"))
AUDIT_JSONL = Path(os.environ.get("DG_AUDIT_JSONL", RUNTIME_DIR / "audit.jsonl"))

# Local inference endpoints (all on the GB10).
LLM_BASE_URL = os.environ.get("DG_LLM_URL", "http://localhost:8000/v1")
LLM_MODEL = os.environ.get("DG_LLM_MODEL", "nvidia/Qwen3.6-35B-A3B-NVFP4")
VLM_BASE_URL = os.environ.get("DG_VLM_URL", "http://localhost:11434")
VLM_MODEL = os.environ.get("DG_VLM_MODEL", "qwen3-vl:30b")


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


@dataclass(frozen=True)
class Rules:
    """Legal ruleset + company policy, flattened for the engines."""

    ruleset_id: str
    ruleset_version: str
    drive_limit: int
    window: int
    reset: int
    break_after: int
    break_len: int
    restart: int
    cycles: dict
    citations: dict
    unsupported_exceptions: tuple
    company_id: str
    company_version: str
    company_drive_limit: int | None
    company_window: int | None
    min_reserve: int
    low_reserve_warn: int
    max_eld_age: int
    min_service_slack: int
    low_slack_warn: int
    recommendation_ttl: int

    def cycle_limit(self, profile: str) -> int:
        return int(self.cycles[profile]["minutes"])

    def cite(self, code: str) -> str:
        return self.citations.get(code, "company policy " + self.company_id)

    def with_overrides(self, **kw) -> "Rules":
        return replace(self, **kw)


@lru_cache
def get_rules() -> Rules:
    legal = load_yaml(CONFIG_DIR / "rulesets" / "us_federal_property_v1.yaml")
    co = load_yaml(CONFIG_DIR / "rulesets" / "company_policy_v1.yaml")
    lim = legal["limits"]
    return Rules(
        ruleset_id=legal["id"],
        ruleset_version=legal["version"],
        drive_limit=lim["driving_minutes"],
        window=lim["window_minutes"],
        reset=lim["off_duty_reset_minutes"],
        break_after=lim["break_after_driving_minutes"],
        break_len=lim["break_minutes"],
        restart=lim["restart_minutes"],
        cycles=lim["cycles"],
        citations=legal["citations"],
        unsupported_exceptions=tuple(legal.get("unsupported_exceptions", [])),
        company_id=co["id"],
        company_version=co["version"],
        company_drive_limit=co.get("max_driving_minutes"),
        company_window=co.get("max_window_minutes"),
        min_reserve=co["min_hos_reserve_minutes"],
        low_reserve_warn=co["low_reserve_warning_minutes"],
        max_eld_age=co["max_eld_age_minutes"],
        min_service_slack=co["min_service_slack_minutes"],
        low_slack_warn=co["low_slack_warning_minutes"],
        recommendation_ttl=co["recommendation_ttl_minutes"],
    )


@dataclass(frozen=True)
class CostModel:
    id: str
    version: str
    coefficients: dict = field(default_factory=dict)  # name -> {value, basis}
    operations: dict = field(default_factory=dict)

    def c(self, name: str) -> float:
        return float(self.coefficients[name]["value"])

    def basis(self, name: str) -> str:
        return self.coefficients[name]["basis"]

    def op(self, name: str) -> int:
        return int(self.operations[name])


@lru_cache
def get_cost_model() -> CostModel:
    raw = load_yaml(CONFIG_DIR / "cost_models" / "default_v1.yaml")
    return CostModel(raw["id"], raw["version"], raw["coefficients"], raw["operations"])


@lru_cache
def get_risk_policy() -> dict:
    return load_yaml(CONFIG_DIR / "risk_policy_v1.yaml")


def policy_versions() -> dict:
    r, c, k = get_rules(), get_cost_model(), get_risk_policy()
    return {
        "ruleset": f"{r.ruleset_id}@{r.ruleset_version}",
        "company_policy": f"{r.company_id}@{r.company_version}",
        "cost_model": f"{c.id}@{c.version}",
        "risk_policy": f"{k['id']}@{k['version']}",
    }
