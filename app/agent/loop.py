"""Conversational agent loop for @mentions.

Deterministic tools own facts (driver status, decision traces, incident
data) -- the local model only explains and converses. It never decides
legality or invents numbers; it's handed real tool output and asked to
phrase it, or given a free-text question it can't match to a tool and
asked to answer honestly that it can't help.

Qwen3.6 is a reasoning model: it writes an internal "thinking" trace before
its real answer. enable_thinking=False turns that off so replies are fast
and the Slack message isn't flooded with scratch reasoning.
"""
from __future__ import annotations

import os
import re

from openai import OpenAI

from app.core.client import CoreClient

LOCAL_MODEL = os.environ.get("DISPATCH_GUARDIAN_MODEL", "nvidia/Qwen3.6-35B-A3B-NVFP4")
LOCAL_BASE_URL = os.environ.get("DISPATCH_GUARDIAN_VLLM_URL", "http://localhost:8000/v1")

SYSTEM_PROMPT = (
    "You are Dispatch Guardian, a trucking dispatch safety assistant. "
    "You NEVER decide legality or invent hours-of-service numbers yourself -- "
    "those come from the tool data given to you below, verbatim. Your job is "
    "only to explain that data clearly and briefly to a dispatcher in Slack. "
    "If no tool data is given and you don't know the answer, say so plainly "
    "instead of guessing."
)


class LocalModelPlanner:
    """Real local inference: calls the vLLM server NemoClaw provisioned on
    this GB10. Tool lookups (get_driver_status, get_decision_trace) still
    run as plain deterministic code -- the model only explains their output,
    it never calls them itself or fabricates data."""

    def __init__(self, core: CoreClient):
        self.core = core
        self.client = OpenAI(base_url=LOCAL_BASE_URL, api_key="not-needed-for-local-vllm")

    def _ask_model(self, user_text: str, tool_context: str | None) -> str:
        system_content = SYSTEM_PROMPT
        if tool_context:
            system_content += "\n\nTool data:\n" + tool_context
        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_text},
        ]
        try:
            resp = self.client.chat.completions.create(
                model=LOCAL_MODEL,
                messages=messages,
                max_tokens=600,
                temperature=0.2,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            content = resp.choices[0].message.content
            if not content:
                return tool_context or "I don't have an answer for that."
            return content.strip()
        except Exception as e:
            return f"(local model unavailable: {e}) Falling back: {tool_context or 'no data found.'}"

    def answer(self, text: str) -> str:
        lowered = text.lower()

        for word in lowered.split():
            status = self.core.get_driver_status(word)
            if status:
                context = (
                    f"Driver {status['name']}, duty status {status['duty_status']}. "
                    f"Drive time left: {status['drive_left_minutes']} min. "
                    f"Shift left: {status['shift_left_minutes']} min. "
                    f"Cycle left: {status['cycle_left_minutes']} min. "
                    f"Endorsements: {', '.join(status['endorsements']) or 'none'}. "
                    f"Note: {status['note']}"
                )
                return self._ask_model(text, context)

        incident_match = re.search(r"dg-i-[\w-]+", lowered)
        if incident_match:
            incident_id = incident_match.group(0).upper()
            trace = self.core.get_decision_trace(incident_id)
            if trace:
                context = "\n".join(f"{i+1}. {s}" for i, s in enumerate(trace))
                return self._ask_model(text, context)
            return f"No decision trace found for {incident_id}."

        return self._ask_model(text, None)


class AgentLoop:
    def __init__(self, core: CoreClient):
        self.planner = LocalModelPlanner(core)

    def handle(self, text: str) -> str:
        return self.planner.answer(text)
