from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable
from datetime import datetime
import uuid

from config import get_settings


@dataclass
class DelegationRequest:
    """Request to delegate a task to Aqua (future automation engine)."""
    delegation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = "research"  # research, automation, workflow
    task: str = ""
    context: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)


@dataclass
class DelegationResult:
    """Result from Aqua delegation."""
    delegation_id: str
    status: str  # completed, failed, partial, not_implemented
    summary: str = ""
    data: dict = field(default_factory=dict)
    next_steps: list[str] = field(default_factory=list)


class AquaClient:
    """
    Stub client for Aqua delegation.
    
    Currently returns not_implemented. When Aqua is repurposed as the
    automation engine, this will be implemented with the same interface
    as LunaClient for consistent orchestration.
    """
    
    def __init__(self, base_url: str = "", api_key: str = ""):
        settings = get_settings()
        self.base_url = base_url or settings.aqua_api_url
        self.api_key = api_key or settings.aqua_api_key
    
    async def delegate_task(
        self,
        task: str,
        task_type: str = "research",
        context: dict | None = None,
        constraints: dict | None = None,
        stream: bool = False,
        on_event: Optional[Callable] = None
    ) -> DelegationResult:
        """Delegate to Aqua — currently returns not_implemented."""
        return DelegationResult(
            delegation_id=str(uuid.uuid4()),
            status="not_implemented",
            summary="Aqua delegation not yet implemented. Aqua will be repurposed as an automation engine in the future.",
            next_steps=["Wait for Aqua automation engine implementation"]
        )
    
    async def health_check(self) -> bool:
        """Check if Aqua is reachable."""
        return False


def get_aqua_client() -> AquaClient:
    """Get the singleton AquaClient."""
    return AquaClient()