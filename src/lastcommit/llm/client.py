"""A minimal OpenAI-compatible chat client over httpx.

Works with Groq, Ollama's /v1 endpoint, vLLM, llama.cpp server, OpenRouter —
anything that speaks POST /chat/completions.  Tool calling included: the
interviewer needs it.
"""

from __future__ import annotations

import json

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, cfg: LLMConfig, timeout: float = 90.0):
        self.cfg = cfg
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        self._http = httpx.Client(base_url=cfg.base_url, headers=headers, timeout=timeout)

    def available(self) -> bool:
        try:
            resp = self._http.get("/models")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> dict:
        """One chat completion. Returns the full response JSON (choice 0)."""
        payload: dict = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            resp = self._http.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(f"LLM returned {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if "choices" not in data:
            raise LLMError(f"unexpected LLM response: {json.dumps(data)[:300]}")
        return data["choices"][0]

    def chat_text(self, messages: list[dict], **kwargs) -> str:
        choice = self.chat(messages, **kwargs)
        return choice["message"].get("content") or ""
