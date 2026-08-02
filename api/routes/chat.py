from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_ai_router, get_busy_mode_manager, get_memory_manager, get_task_manager, get_timetable_manager, get_aqua_manager, get_luna_manager
from core.busy_mode import BusyModeManager
from core.memory import MemoryManager
from core.router import AIRouter, TaskType
from core.schedule import TimetableManager
from core.tasks import TaskManager
from core.timeutil import local_now, local_today



router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    task_type: TaskType = TaskType.CONVERSATION
    system: Optional[str] = None
    # "Manual mode" fields - when provider is set, Emma skips the routing
    # table entirely and uses exactly what the GUI told it to use.
    provider: Optional[str] = None
    model: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    provider: str
    model: str


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

    # Build the full system context: persona + long-term text + project text + daily text.
    # This is what makes Emma actually know about her memories during conversation.
    parts = []
    if payload.system:
        parts.append(payload.system)

    persona = memory.get_persona()
    if persona:
        parts.append(persona)

    # Current time + schedule context so Emma knows what's happening
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
        time_context += f"\nVOID is currently busy" + (f" ({state.note})" if state.note else "")
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

    # Aqua context — tell Emma about her subordinate
    try:
        aqua_mgr = get_aqua_manager()
    except ImportError:
        aqua_mgr = None
    if aqua_mgr:
        try:
            h = await aqua_mgr.health()
            aqua_lines = ["\nYou have a subordinate AI called Aqua specialized in research and study."]
            if h.get("alive"):
                aqua_lines.append(f"Aqua is online at {h.get('url', '?')} (uptime: {h.get('uptime', '?')}s).")
                aqua_lines.append("You can delegate research, study, and document management to her.")
                from core.tools.registry import get_registry
                tools_block = get_registry().system_prompt_block()
                if tools_block:
                    aqua_lines.append("")
                    aqua_lines.append(tools_block)
                    aqua_lines.append("")
                    aqua_lines.append("After executing a tool, I will report the result. Continue normally.")
            else:
                aqua_lines.append("Aqua is configured but not running. You can ask to launch her.")
            parts.append("\n".join(aqua_lines))
        except Exception:
            pass

    # Luna context — tell Emma about her coding subordinate
    try:
        luna_mgr = get_luna_manager()
    except ImportError:
        luna_mgr = None
    if luna_mgr:
        try:
            h = await luna_mgr.health()
            luna_lines = ["\nYou have a subordinate AI called Luna specialized in coding and programming."]
            if h.get("alive"):
                luna_lines.append(f"Luna is online at {h.get('url', '?')} (uptime: {h.get('uptime', '?')}s).")
                luna_lines.append("You can delegate coding, debugging, and programming tasks to her.")
            else:
                luna_lines.append("Luna is configured but not running. You can ask to launch her.")
            luna_coding = memory.get_project_text("luna-coding")
            if luna_coding:
                luna_lines.append(f"\nRecent from Luna's coding sessions:\n{luna_coding[:500]}")
            parts.append("\n".join(luna_lines))
        except Exception:
            pass

    system = "\n\n".join(parts) if parts else None

    try:
        result = await ai_router.run(
            payload.task_type,
            payload.message,
            system=system,
            model=payload.model,
            provider_name=payload.provider,
        )
    except Exception as exc:  # noqa: BLE001 - the router already produced a clean message
        # 503: the request was fine, Emma just has no working brain to answer
        # with right now. Surface the human-readable reason to the UI.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    memory.add_turn(payload.session_id, "assistant", result.text)

    # Execute any [TOOL:...] directives from the response
    from core.tools.executor import execute_directives
    tool_results = await execute_directives(result.text)

    # Append tool results to the reply so the user sees what happened
    reply_text = result.text
    if tool_results:
        summaries = []
        for tr in tool_results:
            if tr["status"] == "ok":
                summaries.append(f"[Aqua: {tr['tool']} — {tr.get('result', 'done')}]")
            elif tr["status"] == "error":
                summaries.append(f"[Aqua: {tr['tool']} failed — {tr.get('error', 'unknown error')}]")
        if summaries:
            reply_text = result.text + "\n\n" + "\n".join(summaries)

    return ChatResponse(reply=reply_text, provider=result.provider, model=result.model)


@router.get("/history/{session_id}")
def get_history(session_id: str, memory: MemoryManager = Depends(get_memory_manager)):
    return memory.get_recent_turns(session_id)
