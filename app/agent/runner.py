"""Minimal typed tool-calling loop against the local planner model."""
from __future__ import annotations

import json
import time

from .llm import LocalLLM
from .tools import ToolBox, compact

SYSTEM = """You are Dispatch Guardian, an operations copilot for a U.S. trucking dispatcher.

Rules you must follow:
- Facts, legality, ETAs, costs and risk come only from tool results. Never compute hours-of-service math or costs yourself and never invent numbers.
- A FAIL verdict from the compliance engine is final. Never suggest bypassing it.
- Schedule risk is a plan-fragility heuristic. Never say or imply a driver is tired, fatigued, or medically unfit.
- Say plainly when data is stale or unknown.
- You cannot approve plans. Assignment changes, relays, equipment swaps, appointment changes and customer commitments need a dispatcher's approval in the dashboard.
- Be brief and concrete: lead with the decision, then the numbers that justify it, then what to watch. Use load, driver and plan IDs. Times in the facility's local time.
"""


def run_agent(toolbox: ToolBox, llm: LocalLLM, user_message: str, context: str | None = None, max_steps: int = 8) -> dict:
    t0 = time.perf_counter()
    # Qwen's chat template accepts a single leading system message only.
    system = SYSTEM + ("\n\nContext from the dispatch system:\n" + context if context else "")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_message}]
    steps: list[dict] = []
    tokens = 0
    for _ in range(max_steps):
        msg = llm.chat(messages, tools=toolbox.specs())
        tokens += msg.get("_usage", {}).get("total_tokens", 0)
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"answer": (msg.get("content") or "").strip(), "steps": steps, "model": llm.model,
                    "elapsed_s": round(time.perf_counter() - t0, 1), "tokens": tokens}
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = toolbox.call(name, args)
            blocked = isinstance(result, dict) and result.get("error") in ("APPROVAL_REQUIRED", "IDEMPOTENCY_KEY_REQUIRED")
            steps.append({"tool": name, "args": {k: v for k, v in args.items() if k != "approval_token"}, "blocked": blocked,
                          "result_preview": compact(result, 400)})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": compact(result)})
    return {"answer": "Stopped after the maximum number of tool steps.", "steps": steps, "model": llm.model,
            "elapsed_s": round(time.perf_counter() - t0, 1), "tokens": tokens}


def explain_incident(toolbox: ToolBox, llm: LocalLLM, incident_id: str) -> dict:
    inc = toolbox.get_incident(incident_id)
    ctx = compact(inc, 9000)
    prompt = (f"Explain incident {incident_id} to the dispatcher in under 170 words. Structure: "
              "1) what broke and the exact rule or constraint, 2) the recommended plan and why it beats each alternative "
              "(ETA/slack, legal reserve, cost), 3) what to watch or confirm before approving. "
              "Use only the numbers in the context or from tools.")
    return run_agent(toolbox, llm, prompt, context=ctx, max_steps=4)
