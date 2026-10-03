"""Clients for the local models on the GB10: the vLLM planner (OpenAI-compatible)
and the Ollama vision model used for document intake. No cloud endpoints."""
from __future__ import annotations

import base64
import time

import httpx

from ..config import LLM_BASE_URL, LLM_MODEL, VLM_BASE_URL, VLM_MODEL


class LocalLLM:
    def __init__(self, base_url: str = LLM_BASE_URL, model: str = LLM_MODEL, timeout: float = 180.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = httpx.Client(timeout=timeout)

    def health(self) -> dict:
        try:
            t = time.perf_counter()
            r = self.http.get(f"{self.base_url}/models", timeout=3)
            r.raise_for_status()
            ids = [m["id"] for m in r.json().get("data", [])]
            return {"ok": self.model in ids, "models": ids, "latency_ms": round((time.perf_counter() - t) * 1000)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def chat(self, messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2,
             max_tokens: int = 1500, thinking: bool = False, response_format: dict | None = None) -> dict:
        body: dict = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens,
                      "chat_template_kwargs": {"enable_thinking": thinking}}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if response_format:
            body["response_format"] = response_format
        r = self.http.post(f"{self.base_url}/chat/completions", json=body)
        r.raise_for_status()
        data = r.json()
        msg = data["choices"][0]["message"]
        msg["_usage"] = data.get("usage", {})
        return msg


class LocalVision:
    def __init__(self, base_url: str = VLM_BASE_URL, model: str = VLM_MODEL, timeout: float = 300.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = httpx.Client(timeout=timeout)

    def health(self) -> dict:
        try:
            r = self.http.get(f"{self.base_url}/api/tags", timeout=3)
            r.raise_for_status()
            names = [m["name"] for m in r.json().get("models", [])]
            return {"ok": self.model in names, "models": names}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def extract(self, image: bytes, prompt: str, schema: dict) -> dict:
        body = {"model": self.model, "stream": False, "format": schema, "options": {"temperature": 0},
                "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(image).decode()]}]}
        r = self.http.post(f"{self.base_url}/api/chat", json=body)
        r.raise_for_status()
        return r.json()
