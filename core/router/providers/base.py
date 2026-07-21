"""
Base contract for every AI provider Emma can talk to.

Adding a new provider (a future API, a new local model runtime, etc.)
means writing one class that implements this interface. Nothing else
in Emma needs to change - that's the whole point of the router pattern.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


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
