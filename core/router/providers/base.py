"""
Base contract for every AI provider Emma can talk to.

Adding a new provider (a future API, a new local model runtime, etc.)
means writing one class that implements this interface. Nothing else
in Emma needs to change - that's the whole point of the router pattern.
"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx


@dataclass
class CompletionResult:
    text: str
    provider: str
    model: str
    raw: Optional[dict] = None


class AIProvider(ABC):
    """Every provider must implement these two things and nothing more."""

    name: str = "base"

    @abstractmethod
    async def is_available(self) -> bool:
        """Cheap health check - can this provider actually serve a request right now?"""
        raise NotImplementedError

    @abstractmethod
    async def complete(self, prompt: str, system: Optional[str] = None, **kwargs) -> CompletionResult:
        """Run a completion and return a normalized result."""
        raise NotImplementedError

    async def stream(self, prompt: str, system: Optional[str] = None, **kwargs) -> AsyncIterator[str]:
        """
        Stream a completion as text chunks. Providers with native streaming
        (Ollama, OpenAI-compatible endpoints) override this; providers that
        can't stream inherit this default, which yields the whole completion
        in one chunk - so streaming code paths never need special cases.
        """
        result = await self.complete(prompt, system=system, **kwargs)
        if result.text:
            yield result.text


async def _openai_stream(url: str, headers: dict, payload: dict) -> AsyncIterator[str]:
    """
    SSE-parsing for OpenAI-compatible /chat/completions streaming endpoints.
    Shared by every OpenAI-shaped provider (local generic, Groq, NIM).
    """
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    # Some servers report failures inside a 200 stream body.
                    raise RuntimeError(str(chunk["error"]))
                choices = chunk.get("choices") or []
                if not choices:
                    # vLLM/llama.cpp/NIM emit keepalive chunks with an empty
                    # choices list; skip, don't crash on the missing index.
                    continue
                delta = choices[0].get("delta", {})
                piece = delta.get("content")
                if piece:
                    yield piece
