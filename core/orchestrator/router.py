from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, AsyncGenerator

from core.orchestrator.intent import IntentClassification, IntentType, classify_intent
from core.orchestrator.luna_client import LunaClient, DelegationResult, DelegationEvent
from core.orchestrator.aqua_client import AquaClient
from core.orchestrator.synthesizer import ResultSynthesizer, SynthesisContext, get_synthesizer


@dataclass
class OrchestrationResult:
    """Result of orchestration decision."""
    handled_by: str  # "emma", "luna", "aqua"
    response: str
    delegation_result: Optional[DelegationResult] = None
    stream_events: Optional[AsyncGenerator[DelegationEvent, None]] = None


class OrchestrationRouter:
    """
    Routes user requests to appropriate handler (Emma, Luna, or Aqua).
    
    Uses LLM-based intent classification to determine delegation.
    """
    
    def __init__(self, ai_router):
        self.ai_router = ai_router
        self.luna_client = LunaClient()
        self.aqua_client = AquaClient()
        self.synthesizer = get_synthesizer()
    
    async def route(
        self,
        user_message: str,
        context: dict | None = None,
        stream: bool = False,
        on_delegation_event: Optional[callable] = None
    ) -> OrchestrationResult:
        """
        Main routing entry point.
        
        Args:
            user_message: User's input
            context: Additional context (project, session, etc.)
            stream: Whether to use streaming for delegations
            on_delegation_event: Callback for streaming delegation events
        
        Returns:
            OrchestrationResult with response and metadata
        """
        # Classify intent
        intent = await classify_intent(user_message, self.ai_router, context)
        
        # Route based on intent
        if intent.suggested_agent == "luna" and intent.primary in (
            IntentType.CODE, IntentType.DEBUG, IntentType.REFACTOR, IntentType.GIT
        ):
            return await self._delegate_to_luna(
                user_message, intent, context, stream, on_delegation_event
            )
        elif intent.suggested_agent == "aqua" and intent.primary in (
            IntentType.RESEARCH, IntentType.AUTOMATION
        ):
            return await self._delegate_to_aqua(
                user_message, intent, context, stream
            )
        else:
            # Handle directly with Emma
            return await self._handle_with_emma(user_message, intent, context)
    
    async def _delegate_to_luna(
        self,
        user_message: str,
        intent: IntentClassification,
        context: dict | None,
        stream: bool,
        on_delegation_event: Optional[callable]
    ) -> OrchestrationResult:
        """Delegate coding task to Luna."""
        
        # Build Luna context
        luna_context = self._build_luna_context(context, intent.primary)
        
        # Delegate
        result = await self.luna_client.delegate_task(
            task=user_message,
            task_type=intent.primary.value,
            context=luna_context,
            constraints=intent.constraints,
            stream=stream,
            on_event=on_delegation_event
        )
        
        # Synthesize natural response
        synth_ctx = SynthesisContext(
            user_message=user_message,
            intent_type=intent.primary.value,
            delegated_to="luna",
            delegation_result=result,
            conversation_history=context.get("history") if context else None
        )
        response = self.synthesizer.synthesize(synth_ctx)
        
        return OrchestrationResult(
            handled_by="luna",
            response=response,
            delegation_result=result
        )
    
    async def _delegate_to_aqua(
        self,
        user_message: str,
        intent: IntentClassification,
        context: dict | None,
        stream: bool
    ) -> OrchestrationResult:
        """Delegate to Aqua (stub for future)."""
        result = await self.aqua_client.delegate_task(
            task=user_message,
            task_type=intent.primary.value,
            context=context or {},
            constraints=intent.constraints
        )
        
        # Aqua not implemented yet — natural fallback
        response = "I'd normally delegate that to Aqua for research/automation, but she's being repurposed as an automation engine. For now, let me help you directly or ask Luna if it's code-related."
        
        return OrchestrationResult(
            handled_by="aqua",
            response=response,
            delegation_result=result
        )
    
    async def _handle_with_emma(
        self,
        user_message: str,
        intent: IntentClassification,
        context: dict | None
    ) -> OrchestrationResult:
        """Handle request directly with Emma's AI."""
        # This will be called by Emma's chat route which has the full AI router
        # Return signal to handle locally
        return OrchestrationResult(
            handled_by="emma",
            response="",  # Empty = handle locally
            delegation_result=None
        )
    
    def _build_luna_context(self, context: dict | None, intent_type: IntentType) -> dict:
        """Build context for Luna delegation."""
        luna_context = {}
        
        if context:
            # Project context
            if context.get("project_path"):
                luna_context["project_path"] = context["project_path"]
            if context.get("relevant_files"):
                luna_context["relevant_files"] = context["relevant_files"]
            if context.get("git_branch"):
                luna_context["git_branch"] = context["git_branch"]
            if context.get("recent_changes"):
                luna_context["recent_changes"] = context["recent_changes"]
            
            # Task-specific context
            if intent_type == IntentType.DEBUG:
                luna_context["require_tests"] = True
            elif intent_type == IntentType.REFACTOR:
                luna_context["require_tests"] = True
            elif intent_type == IntentType.GIT:
                luna_context["max_duration_seconds"] = 60
        
        return luna_context
    
    async def close(self):
        """Clean up clients."""
        await self.luna_client.close()

    async def route_stream(
        self,
        user_message: str,
        context: dict | None = None,
        stream: bool = False,
    ):
        """
        Streaming version of route that yields events for real-time updates.
        
        Yields events like:
        - {"type": "text", "text": "..."} - Text chunks
        - {"type": "delegation_started", "delegation_id": "...", "target": "luna"}
        - {"type": "tool_start", "tool": "...", "args": {...}}
        - {"type": "tool_end", "tool": "...", "result_preview": "..."}
        - {"type": "delegation_completed", "delegation_id": "...", "status": "...", "summary": "...", ...}
        - {"type": "delegation_failed", "delegation_id": "...", "error": "..."}
        - {"type": "text", "text": "..."} - Regular text chunks
        - {"type": "error", "detail": "..."}
        """
        intent = await classify_intent(user_message, self.ai_router, context)
        
        if intent.suggested_agent == "luna" and intent.primary in (
            IntentType.CODE, IntentType.DEBUG, IntentType.REFACTOR, IntentType.GIT
        ):
            # Delegate to Luna with streaming
            async for event in self._delegate_to_luna_stream(user_message, intent, context):
                yield event
        elif intent.suggested_agent == "aqua" and intent.primary in (
            IntentType.RESEARCH, IntentType.AUTOMATION
        ):
            # Delegate to Aqua (stub)
            yield {"type": "delegation_failed", "delegation_id": "stub", "error": "Aqua not implemented"}
        else:
            # Handle with Emma directly
            async for event in self._stream_emma_response(user_message, context):
                yield event

    async def _delegate_to_luna_stream(
        self,
        user_message: str,
        intent: IntentClassification,
        context: dict | None,
    ):
        """Stream delegation to Luna with real-time events."""
        from core.observability import get_tracer, trace_span
        
        tracer = get_tracer("emma")
        luna_context = self._build_luna_context(context, intent.primary)
        delegation_id = __import__('uuid').uuid4().hex[:16]
        
        with trace_span("emma", "delegation_stream", tags={"target": "luna", "delegation_id": delegation_id}):
            yield {
                "type": "delegation_started",
                "delegation_id": delegation_id,
                "target": "luna",
            }
            
            # Build Luna context
            luna_ctx = self._build_luna_context(context, intent.primary)
            
            # Delegate with streaming
            try:
                async for event in self.luna_client.delegate_task(
                    task=user_message,
                    task_type=intent.primary.value,
                    context=luna_ctx,
                    constraints=intent.constraints,
                    stream=True,
                ):
                    # Convert Luna's events to our event format
                    if isinstance(event, DelegationEvent):
                        if event.event_type == "chunk":
                            yield {"type": "delegation_chunk", "delegation_id": delegation_id, "text": event.text}
                        elif event.event_type == "tool_start":
                            yield {
                                "type": "tool_start",
                                "tool": event.name,
                                "args": event.arguments,
                                "delegation_id": delegation_id,
                            }
                        elif event.event_type == "tool_end":
                            yield {
                                "type": "tool_end",
                                "tool": event.name,
                                "result_preview": str(event.result)[:200] if event.result else "",
                                "delegation_id": delegation_id,
                            }
                        elif event.event_type == "completed":
                            yield {
                                "type": "delegation_completed",
                                "delegation_id": delegation_id,
                                "status": event.data.get("status", "completed"),
                                "summary": event.data.get("summary", ""),
                                "files_changed": event.data.get("files_changed", []),
                                "tests_run": event.data.get("tests_run", 0),
                                "tests_passed": event.data.get("tests_passed", 0),
                            }
                        elif event.event_type == "failed":
                            yield {
                                "type": "delegation_failed",
                                "delegation_id": delegation_id,
                                "error": event.data.get("error", "Unknown error"),
                            }
            except Exception as e:
                yield {"type": "delegation_failed", "delegation_id": delegation_id, "error": str(e)}
    
    async def _stream_emma_response(self, user_message: str, context: dict | None):
        """Stream Emma's direct response."""
        # Use the AI router's stream method
        async for piece in self.ai_router.stream(
            TaskType.CONVERSATION,
            user_message,
            system=context.get("system") if context else None,
            local_only=context.get("local_only", False),
        ):
            if hasattr(piece, 'text'):
                yield {"type": "text", "text": piece.text}
            elif isinstance(piece, str):
                yield {"type": "text", "text": piece}
    
    async def close(self):
        """Clean up clients."""
        await self.luna_client.close()


def get_orchestration_router(ai_router) -> OrchestrationRouter:
    """Get singleton orchestration router."""
    return OrchestrationRouter(ai_router)