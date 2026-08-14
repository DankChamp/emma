"""
Local provider - talks to Ollama running on the user's own machine.
This is Emma's "Mode 2: Local" - works with no internet at all.
"""
import json
from typing import AsyncIterator, Optional

import httpx

from .base import AIProvider, CompletionResult


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(self, base_url: str, default_model: str):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> CompletionResult:
        model = kwargs.get("model", self.default_model)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = data.get("message", {}).get("content", "")
        return CompletionResult(text=text, provider=self.name, model=model, raw=data)

    async def stream(self, prompt: str, system: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
        model = kwargs.get("model", self.default_model)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise RuntimeError(str(data["error"]))
                    piece = data.get("message", {}).get("content", "")
                    if piece:
                        yield piece
