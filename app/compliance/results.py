"""Typed result shapes for the compliance engine. Plain dicts, built by helper
constructors so every call site produces the same shape -- this is what the
local model reads; it must never have to guess the schema."""
from __future__ import annotations


def violation(rule: str, required_minutes: int, available_minutes: int, detail: str = "") -> dict:
    return {
        "rule": rule,
        "required_minutes": required_minutes,
        "available_minutes": available_minutes,
        "deficit_minutes": max(0, required_minutes - available_minutes),
        "detail": detail,
    }


def warning(rule: str, detail: str) -> dict:
    return {"rule": rule, "detail": detail}


def compliance_result(
    verdict: str,
    violations: list[dict] | None = None,
    warnings: list[dict] | None = None,
    unsupported_exceptions: list[str] | None = None,
    data_freshness: dict | None = None,
) -> dict:
    assert verdict in ("legal", "illegal", "manual_review")
    return {
        "verdict": verdict,
        "violations": violations or [],
        "warnings": warnings or [],
        "unsupported_exceptions": unsupported_exceptions or [],
        "data_freshness": data_freshness or {},
    }
