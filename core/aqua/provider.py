"""
AquaProvider — treats Aqua as an AI provider in Emma's router.

Emma can delegate research/study tasks to Aqua by routing through this
provider. Aqua answers through her own chain of providers (ollama, groq, etc.)
so this effectively lets Emma chain into a subordinate AI's reasoning.
"""
from typing import Optional

import httpx

from core.router.providers.base import AIProvider, CompletionResult


class AquaProvider(AIProvider):
    name = "aqua"

    def __init__(self, api_url: str, api_key: str = "", default_model: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.api_url}/health", headers=self._headers())
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> CompletionResult:
        model = kwargs.get("model", self.default_model) or "conversation"
        task_type = kwargs.get("task_type", "conversation")

        payload = {"message": prompt, "task_type": task_type}
        if system:
            payload["system"] = system
        if model:
            payload["model"] = model

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{self.api_url}/chat", json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        text = data.get("reply", "")
        return CompletionResult(
            text=text,
            provider=self.name,
            model=data.get("model", model),
            raw=data,
        )
