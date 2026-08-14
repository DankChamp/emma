import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.deps import get_ai_router, get_busy_mode_manager, get_memory_manager, get_task_manager, get_timetable_manager, get_aqua_manager, get_luna_manager
from core.busy_mode import BusyModeManager
from core.memory import MemoryManager
from core.router import AIRouter, TaskType
from core.schedule import TimetableManager
from core.tasks import TaskManager
from core.timeutil import local_now, local_today
from core.orchestrator.router import get_orchestration_router, OrchestrationResult
from core.orchestrator.intent import IntentType
from core.orchestrator.synthesizer import get_synthesizer

logger = logging.getLogger("emma.api.chat")


router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    task_type: TaskType = TaskType.CONVERSATION
    system: Optional[str] = None
    stream: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    local_only: bool = False


class ChatResponse(BaseModel):
    reply: str
    provider: str
    model: str
    delegated: bool = False
    delegation_id: Optional[str] = None


class JudgeRequest(BaseModel):
    message: str
    local_only: bool = False


class JudgeResponse(BaseModel):
    should_respond: bool
    intent: str


JUDGE_SYSTEM = (
    "You are the intent gate for a wake-word voice assistant called Emma. "
    "The user just said her wake word and then the following utterance. "
    "Decide whether they are clearly addressing Emma with a request, "
    "question, or instruction. Say no if they appear to be talking to "
    "someone else, muttering to themselves, or saying something obviously "
    "not meant for the assistant.\n"
    "Reply with ONLY JSON, no prose, of the exact form:\n"
    '{"should_respond": true, "intent": "<one short phrase describing the request>"}'
)


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    ai_router: AIRouter = Depends(get_ai_router),
    memory: MemoryManager = Depends(get_memory_manager),
    tasks: TaskManager = Depends(get_task_manager),
    timetable: TimetableManager = Depends(get_timetable_manager),
    busy_mode: BusyModeManager = Depends(get_busy_mode_manager),
):
    memory.add_turn(payload.session_id, "user", payload.message)

    now = local_now()
    today = local_today()

    # Build the full system context
    parts = []
    if payload.system:
        parts.append(payload.system)

    persona = memory.get_persona()
    if persona:
        parts.append(persona)

    # Current time + schedule context
    time_context = f"Current date and time: {today.isoformat()} {now.strftime('%H:%M')} ({today.strftime('%A')})"
    blocks = timetable.list_day(today)
    if blocks:
        block_lines = []
        for b in blocks:
            status = "busy" if b.busy else "free"
            block_lines.append(f"  {b.start.strftime('%H:%M')}–{b.end.strftime('%H:%M')}  {b.title}  ({status})")
        time_context += "\nToday's schedule:\n" + "\n".join(block_lines)
    state = busy_mode.get_state()
    if state.is_busy:
        time_context += (f"\nVOID is currently busy{(' (' + state.note + ')') if state.note else ''}")
    parts.append(time_context)

    long_text = memory.get_long_term_text()
    if long_text:
        parts.append(f"Long-term memory:\n{long_text}")

    active_project = memory.get_active_project()
    if active_project:
        project_text = memory.get_project_text(active_project)
        if project_text:
            parts.append(f"Active project ({active_project}):\n{project_text}")

    daily_text = memory.get_daily_text()
    if daily_text:
        parts.append(f"Today's context:\n{daily_text}")

    task_summary = tasks.pending_summary()
    if task_summary:
        parts.append(f"The user's open tasks (from their task manager):\n{task_summary}")

    # Subordinate AI context
    try:
        aqua_mgr = get_aqua_manager()
    except ImportError:
        aqua_mgr = None
    if aqua_mgr:
        try:
            h = await aqua_mgr.health()
            aqua_lines = ["\nYou have a subordinate AI called Aqua (research/study)."]
            if h.get("alive"):
                aqua_lines.append(f"Aqua is online at {h.get('url', '?')} (uptime: {h.get('uptime', '?')}s).")
            else:
                aqua_lines.append("Aqua is configured but not running.")
            parts.append("\n".join(aqua_lines))
        except Exception as exc:
            logging.getLogger("emma.api.chat").debug("Aqua context skipped: %s", exc)

    try:
        luna_mgr = get_luna_manager()
    except ImportError:
        luna_mgr = None
    if luna_mgr:
        try:
            h = await luna_mgr.health()
            luna_lines = ["\nYou have a subordinate AI called Luna (coding specialist)."]
            if h.get("alive"):
                luna_lines.append(f"Luna is online at {h.get('url', '?')} (uptime: {h.get('uptime', '?')}s).")
            else:
                luna_lines.append("Luna is configured but not running.")
            luna_coding = memory.get_project_text("luna-coding")
            if luna_coding:
                luna_lines.append(f"\nRecent from Luna's coding sessions:\n{luna_coding[:500]}")
            parts.append("\n".join(luna_lines))
        except Exception as exc:
            logging.getLogger("emma.api.chat").debug("Luna context skipped: %s", exc)

    system = "\n\n".join(parts) if parts else None

    # Build orchestration context
    orchestration_context = {
        "history": memory.get_recent_turns(payload.session_id),
        "project_path": active_project,
        "git_branch": "",  # Could be extracted from project
        "local_only": payload.local_only,
    }

    # Check if we should use orchestration (skip for manual provider/model)
    use_orchestration = not (payload.provider and payload.model)

    if use_orchestration:
        orchestrator = get_orchestration_router(ai_router)
        
        # Route through orchestration
        result = await orchestrator.route(
            user_message=payload.message,
            context=orchestration_context,
            stream=payload.stream
        )
        
        if result.handled_by == "luna" and result.delegation_result:
            # Delegated to Luna — return synthesized response
            memory.add_turn(payload.session_id, "assistant", result.response)
            return ChatResponse(
                reply=result.response,
                provider=result.delegation_result.status,
                model=f"luna:{result.delegation_result.status}",
                delegated=True,
                delegation_id=result.delegation_result.delegation_id
            )
        elif result.handled_by == "aqua":
            # Aqua delegation (stub)
            memory.add_turn(payload.session_id, "assistant", result.response)
            return ChatResponse(
                reply=result.response,
                provider="aqua",
                model="aqua:stub",
                delegated=True,
                delegation_id=result.delegation_result.delegation_id if result.delegation_result else None
            )
        # else handled by Emma — fall through to normal processing
        await orchestrator.close()

    # Streaming mode
    if payload.stream:
        # Check if we should use orchestration for streaming
        use_orchestration = not (payload.provider and payload.model)
        
        if use_orchestration:
            orchestrator = get_orchestration_router(ai_router)
            return StreamingResponse(
                _chat_stream_with_orchestration(payload, system, ai_router, memory, orchestrator),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        
        return StreamingResponse(
            _chat_stream(payload, system, ai_router, memory),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Normal Emma handling
    try:
        result = await ai_router.run(
            payload.task_type,
            payload.message,
            system=system,
            model=payload.model,
            provider_name=payload.provider,
            local_only=payload.local_only,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    memory.add_turn(payload.session_id, "assistant", result.text)

    # Execute any [TOOL:...] directives
    from core.tools.executor import execute_directives
    tool_results = await execute_directives(result.text)

    reply_text = result.text
    if tool_results:
        summaries = []
        for tr in tool_results:
            if tr["status"] == "ok":
                summaries.append(f"[Tool: {tr['tool']} — {tr.get('result', 'done')}]")
            elif tr["status"] == "error":
                summaries.append(f"[Tool: {tr['tool']} failed — {tr.get('error', 'unknown error')}]")
        if summaries:
            reply_text = result.text + "\n\n" + "\n".join(summaries)

    return ChatResponse(reply=reply_text, provider=result.provider, model=result.model, delegated=False)


@router.get("/history/{session_id}")
def get_history(session_id: str, memory: MemoryManager = Depends(get_memory_manager)):
    return memory.get_recent_turns(session_id)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _chat_stream_with_orchestration(
    payload: ChatRequest,
    system: Optional[str],
    ai_router: AIRouter,
    memory: MemoryManager,
    orchestrator,
):
    """
    SSE generator with orchestration support. Handles delegation streaming
    with proper event types for tool execution, chunks, and completion.
    """
    full_text: list[str] = []
    
    try:
        async for event in orchestrator.route_stream(
            user_message=payload.message,
            context={
                "history": memory.get_recent_turns(payload.session_id),
                "project_path": None,
                "git_branch": "",
                "local_only": payload.local_only,
            },
            stream=True,
        ):
            # Handle different event types from orchestrator
            if event.get("type") == "chunk":
                # Text chunk from Emma or delegated agent
                yield _sse({"event": "text", "text": event.get("text", "")})
            
            elif event.get("type") == "delegation_started":
                yield _sse({
                    "event": "delegation_started",
                    "delegation_id": event.get("delegation_id"),
                    "target": event.get("target"),
                })
            
            elif event.get("type") == "tool_start":
                yield _sse({
                    "event": "tool_start",
                    "tool": event.get("tool"),
                    "args": event.get("args"),
                    "delegation_id": event.get("delegation_id"),
                })
            
            elif event.get("type") == "tool_end":
                yield _sse({
                    "event": "tool_end",
                    "tool": event.get("tool"),
                    "result_preview": event.get("result_preview"),
                    "delegation_id": event.get("delegation_id"),
                })
            
            elif event.get("type") == "delegation_chunk":
                # Text chunk from delegated agent
                yield _sse({"event": "text", "text": event.get("text", "")})
            
            elif event.get("type") == "delegation_completed":
                yield _sse({
                    "event": "delegation_completed",
                    "delegation_id": event.get("delegation_id"),
                    "status": event.get("status"),
                    "summary": event.get("summary"),
                    "files_changed": event.get("files_changed", []),
                    "tests_run": event.get("tests_run", 0),
                    "tests_passed": event.get("tests_passed", 0),
                })
            
            elif event.get("type") == "delegation_failed":
                yield _sse({
                    "event": "delegation_failed",
                    "delegation_id": event.get("delegation_id"),
                    "error": event.get("error"),
                })
            
            elif event.get("type") == "text":
                # Regular text chunk from Emma
                yield _sse({"event": "text", "text": event.get("text", "")})
            
            elif event.get("type") == "error":
                yield _sse({"event": "error", "detail": event.get("detail", "Unknown error")})
                return
        
    except Exception as exc:
        logger.exception("Streaming chat with orchestration failed")
        yield _sse({"event": "error", "detail": str(exc)[:300]})
        return
    
    # For non-delegated responses, we still need to add to memory
    # The final response would have been handled above
    # Note: In practice, we'd track the full response and add to memory here


@router.post("/judge", response_model=JudgeResponse, name="chat_judge")
async def judge(
    payload: JudgeRequest,
    ai_router: AIRouter = Depends(get_ai_router),
):
    """
    Intent gate for the voice loop: the wake word being heard doesn't mean
    the user is talking to Emma, so before she answers, this lightweight
    call decides whether the utterance is actually addressed to her. No
    history is recorded here and no tools run. The JSON reply is consumed
    programmatically by the voice loop - it is never spoken.
    """
    try:
        result = await ai_router.run(
            TaskType.GENERAL_ASSISTANT,
            payload.message,
            system=JUDGE_SYSTEM,
            local_only=payload.local_only,
        )
    except Exception as exc:  # noqa: BLE001 - no brain available: answer anyway rather than go mute
        logger.info("Judge couldn't reach a provider (%s); answering anyway.", exc)
        return JudgeResponse(should_respond=True, intent="")

    text = result.text.strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
        # Accept a real bool, or a stringified one ("false"/"no"/"0"): a bare
        # bool("false") is True, which would make the gate say "respond"
        # exactly when the model said not to.
        verdict = data.get("should_respond", True)
        if isinstance(verdict, bool):
            should_respond = verdict
        else:
            should_respond = str(verdict).strip().lower() in {"true", "yes", "1"}
        intent = str(data.get("intent", ""))[:120]
    except (json.JSONDecodeError, AttributeError):
        should_respond, intent = True, ""
    return JudgeResponse(should_respond=should_respond, intent=intent)
