"""Conversational agent loop for @mentions.

The model explains; tools own facts. For now the "model" is a deterministic
keyword router over read-only core tools so the Slack layer is demo-able
today. On hackathon day, replace StubPlanner with a NemoClaw/local-inference
backend that does real tool-calling — the AgentLoop interface stays the same.
"""

from __future__ import annotations

import re

from app.core.client import CoreClient


class StubPlanner:
    """Placeholder for the local planner model on the GB10."""

    def __init__(self, core: CoreClient):
        self.core = core

    def answer(self, text: str) -> str:
        lowered = text.lower()

        driver_match = re.search(r"\bd-?(\d{1,2})\b", lowered)
        if driver_match and any(w in lowered for w in ("status", "driver", "clock", "hours")):
            driver_id = f"D-{int(driver_match.group(1)):02d}"
            status = self.core.get_driver_status(driver_id)
            if not status:
                return f"I don't have a record for {driver_id}."
            return (
                f"*{driver_id} — {status['name']}* ({status['duty_status']})\n"
                f"Drive left: {status['drive_left_minutes']} min · "
                f"Shift left: {status['shift_left_minutes']} min · "
                f"Cycle left: {status['cycle_left_minutes']} min\n"
                f"Endorsements: {', '.join(status['endorsements'])}\n"
                f"{status['note']}"
            )

        incident_match = re.search(r"dg-i-\d+", lowered)
        if incident_match and any(w in lowered for w in ("why", "trace", "explain", "decision")):
            incident_id = incident_match.group(0).upper()
            trace = self.core.get_decision_trace(incident_id)
            if not trace:
                return f"No decision trace found for {incident_id}."
            steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(trace))
            return f"*Decision trace for {incident_id}:*\n{steps}"

        return (
            "I can answer things like:\n"
            "• `status of D-03`\n"
            "• `why was DG-I-0042 flagged?`\n"
            "Compliance verdicts come from the deterministic rules engine — "
            "I only explain them."
        )


class AgentLoop:
    def __init__(self, core: CoreClient):
        self.planner = StubPlanner(core)

    def handle(self, text: str) -> str:
        return self.planner.answer(text)
