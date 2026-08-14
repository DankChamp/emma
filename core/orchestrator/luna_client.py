from __future__ import annotations
import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, AsyncGenerator, Callable, Awaitable
from contextlib import asynccontextmanager

import httpx
import websockets
from websockets.client import WebSocketClientProtocol

from config import get_settings


@dataclass
class DelegationRequest:
    """Request to delegate a task to Luna."""
    delegation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    task_type: str = "code"  # code, debug, refactor, git, plan
    task: str = ""
    context: dict = field(default_factory=dict)
    constraints: dict = field(default_factory=dict)
    callback_url: Optional[str] = None


@dataclass
class DelegationEvent:
    """Event from Luna during delegation."""
    event_type: str  # "started", "tool_start", "tool_end", "chunk", "completed", "failed"
    delegation_id: str
    data: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class DelegationResult:
    """Final result of a delegation."""
    delegation_id: str
    status: str  # completed, failed, partial
    summary: str = ""
    files_changed: list[str] = field(default_factory=list)
    tests_run: int = 0
    tests_passed: int = 0
    next_steps: list[str] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    raw_events: list[DelegationEvent] = field(default_factory=list)


class LunaClient:
    """
    High-level client for delegating tasks to Luna with WebSocket streaming.
    
    Uses persistent REPL session mode — delegated tasks run in Luna's
    active session, maintaining context across delegations.
    """
    
    def __init__(self, base_url: str = "", api_key: str = ""):
        settings = get_settings()
        self.base_url = base_url or settings.luna_api_url
        self.api_key = api_key or settings.luna_api_key
        self._http_client: Optional[httpx.AsyncClient] = None
        self._ws: Optional[WebSocketClientProtocol] = None
        self._ws_connected = asyncio.Event()
        self._event_queue: asyncio.Queue[DelegationEvent] = asyncio.Queue()
        self._ws_task: Optional[asyncio.Task] = None
        self._pending_delegations: dict[str, asyncio.Future[DelegationResult]] = {}
    
    async def _ensure_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                timeout=30.0
            )
        return self._http_client
    
    async def _ensure_ws_connection(self) -> WebSocketClientProtocol:
        """Establish WebSocket connection for streaming events."""
        if self._ws is not None and not self._ws.closed:
            return self._ws
        
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/ws"
        
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        self._ws = await websockets.connect(ws_url, extra_headers=headers)
        self._ws_connected.set()
        
        # Start background listener
        if self._ws_task is None or self._ws_task.done():
            self._ws_task = asyncio.create_task(self._ws_listener())
        
        return self._ws
    
    async def _ws_listener(self):
        """Background task to receive WebSocket events."""
        try:
            async for message in self._ws:
                try:
                    data = json.loads(message)
                    event = DelegationEvent(
                        event_type=data.get("type", "unknown"),
                        delegation_id=data.get("delegation_id", ""),
                        data=data
                    )
                    await self._event_queue.put(event)
                    
                    # Resolve pending delegation if completed
                    dep_id = event.delegation_id
                    if dep_id in self._pending_delegations and event.event_type in ("completed", "failed"):
                        future = self._pending_delegations.pop(dep_id)
                        # Build result from accumulated events
                        # (simplified - in practice would accumulate all events)
                        result = DelegationResult(
                            delegation_id=dep_id,
                            status="completed" if event.event_type == "completed" else "failed",
                            summary=data.get("summary", ""),
                            files_changed=data.get("files_changed", []),
                            tests_run=data.get("tests_run", 0),
                            tests_passed=data.get("tests_passed", 0),
                            next_steps=data.get("next_steps", []),
                            artifacts=data.get("artifacts", {})
                        )
                        future.set_result(result)
                except json.JSONDecodeError:
                    pass
                except Exception:
                    pass
        except websockets.exceptions.ConnectionClosed:
            self._ws_connected.clear()
        except Exception:
            self._ws_connected.clear()
    
    @asynccontextmanager
    async def _delegation_stream(self, delegation_id: str) -> AsyncGenerator[DelegationEvent, None]:
        """Context manager for streaming events for a specific delegation."""
        # Create future for final result
        future: asyncio.Future[DelegationResult] = asyncio.Future()
        self._pending_delegations[delegation_id] = future
        
        try:
            # Yield events as they come
            while not future.done():
                event = await asyncio.wait_for(self._event_queue.get(), timeout=1.0)
                if event.delegation_id == delegation_id:
                    yield event
                    if event.event_type in ("completed", "failed"):
                        break
        except asyncio.TimeoutError:
            pass
        finally:
            self._pending_delegations.pop(delegation_id, None)
    
    async def delegate_task(
        self,
        task: str,
        task_type: str = "code",
        context: dict | None = None,
        constraints: dict | None = None,
        stream: bool = True,
        on_event: Optional[Callable[[DelegationEvent], Awaitable[None]]] = None
    ) -> DelegationResult:
        """
        Delegate a task to Luna.
        
        Args:
            task: The task description
            task_type: code, debug, refactor, git, plan
            context: Project context (path, files, git state)
            constraints: max_duration, require_tests, etc.
            stream: Whether to use WebSocket streaming
            on_event: Optional async callback for each event
        
        Returns:
            DelegationResult with summary and details
        """
        delegation_id = str(uuid.uuid4())
        
        request = DelegationRequest(
            delegation_id=delegation_id,
            task_type=task_type,
            task=task,
            context=context or {},
            constraints=constraints or {}
        )
        
        if stream:
            return await self._delegate_streaming(request, on_event)
        else:
            return await self._delegate_http(request)
    
    async def _delegate_streaming(
        self,
        request: DelegationRequest,
        on_event: Optional[Callable[[DelegationEvent], Awaitable[None]]] = None
    ) -> DelegationResult:
        """Delegate via WebSocket for real-time streaming."""
        ws = await self._ensure_ws_connection()
        
        # Send delegation request
        await ws.send(json.dumps({
            "type": "delegate",
            "delegation_id": request.delegation_id,
            "task_type": request.task_type,
            "task": request.task,
            "context": request.context,
            "constraints": request.constraints
        }))
        
        # Stream events
        async for event in self._delegation_stream(request.delegation_id):
            if on_event:
                await on_event(event)
            
            if event.event_type in ("completed", "failed"):
                # Build final result from event data
                data = event.data
                return DelegationResult(
                    delegation_id=request.delegation_id,
                    status="completed" if event.event_type == "completed" else "failed",
                    summary=data.get("summary", ""),
                    files_changed=data.get("files_changed", []),
                    tests_run=data.get("tests_run", 0),
                    tests_passed=data.get("tests_passed", 0),
                    next_steps=data.get("next_steps", []),
                    artifacts=data.get("artifacts", {}),
                    raw_events=[event]
                )
        
        # Fallback if stream ends unexpectedly
        return DelegationResult(
            delegation_id=request.delegation_id,
            status="failed",
            summary="Stream ended unexpectedly"
        )
    
    async def _delegate_http(self, request: DelegationRequest) -> DelegationResult:
        """Delegate via HTTP (fallback, no streaming)."""
        client = await self._ensure_http_client()
        
        response = await client.post(
            "/api/delegate",
            json={
                "delegation_id": request.delegation_id,
                "task_type": request.task_type,
                "task": request.task,
                "context": request.context,
                "constraints": request.constraints
            }
        )
        response.raise_for_status()
        data = response.json()
        
        return DelegationResult(
            delegation_id=request.delegation_id,
            status=data.get("status", "completed"),
            summary=data.get("summary", ""),
            files_changed=data.get("files_changed", []),
            tests_run=data.get("tests_run", 0),
            tests_passed=data.get("tests_passed", 0),
            next_steps=data.get("next_steps", []),
            artifacts=data.get("artifacts", {})
        )
    
    async def get_status(self) -> dict:
        """Get Luna's status via HTTP."""
        client = await self._ensure_http_client()
        response = await client.get("/status")
        response.raise_for_status()
        return response.json()
    
    async def health_check(self) -> bool:
        """Check if Luna is reachable."""
        try:
            await self.get_status()
            return True
        except Exception:
            return False
    
    async def close(self):
        """Clean up connections."""
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


# Singleton instance getter
_luna_client_instance: Optional[LunaClient] = None


def get_luna_client() -> LunaClient:
    """Get or create the singleton LunaClient."""
    global _luna_client_instance
    if _luna_client_instance is None:
        _luna_client_instance = LunaClient()
    return _luna_client_instance