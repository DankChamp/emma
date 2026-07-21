from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_ai_router, get_memory_manager, get_task_manager
from core.memory import MemoryManager
from core.router import AIRouter, TaskType
from core.tasks import TaskManager



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
):
    memory.add_turn(payload.session_id, "user", payload.message)

    # Build the full system context: persona + long-term text + project text + daily text.
    # This is what makes Emma actually know about her memories during conversation.
    parts = []
    if payload.system:
        parts.append(payload.system)

    persona = memory.get_persona()
    if persona:
        parts.append(persona)

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

    return ChatResponse(reply=result.text, provider=result.provider, model=result.model)


@router.get("/history/{session_id}")
def get_history(session_id: str, memory: MemoryManager = Depends(get_memory_manager)):
    return memory.get_recent_turns(session_id)
