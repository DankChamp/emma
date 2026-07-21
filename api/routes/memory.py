from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_memory_manager
from core.memory import MemoryManager

router = APIRouter(prefix="/memory", tags=["memory"])


class LongTermSet(BaseModel):
    category: str
    key: str
    value: str


class ProjectSet(BaseModel):
    project: str
    key: str
    value: str


class DailySet(BaseModel):
    key: str
    value: str


class PersonaSet(BaseModel):
    text: str


class FreeformText(BaseModel):
    text: str


class ProjectTextSet(BaseModel):
    project: str
    text: str


class ProjectRename(BaseModel):
    old: str
    new: str


class ActiveProjectSet(BaseModel):
    project: Optional[str] = None


@router.get("/long-term-text")
def get_long_term_text(memory: MemoryManager = Depends(get_memory_manager)):
    return {"text": memory.get_long_term_text()}


@router.post("/long-term-text")
def set_long_term_text(payload: FreeformText, memory: MemoryManager = Depends(get_memory_manager)):
    memory.set_long_term_text(payload.text)
    return {"ok": True}


@router.get("/project-text/{project}")
def get_project_text(project: str, memory: MemoryManager = Depends(get_memory_manager)):
    return {"text": memory.get_project_text(project)}


@router.post("/project-text")
def set_project_text(payload: ProjectTextSet, memory: MemoryManager = Depends(get_memory_manager)):
    memory.set_project_text(payload.project, payload.text)
    return {"ok": True}


@router.get("/daily-text")
def get_daily_text(memory: MemoryManager = Depends(get_memory_manager)):
    return {"text": memory.get_daily_text()}


@router.post("/daily-text")
def set_daily_text(payload: FreeformText, memory: MemoryManager = Depends(get_memory_manager)):
    memory.set_daily_text(payload.text)
    return {"ok": True}


@router.get("/persona")
def get_persona(memory: MemoryManager = Depends(get_memory_manager)):
    """
    Emma's free-form identity: who she is, who you are, what her purpose is.
    Returned to the GUI's Identity editor, and fed to every model as the
    system prompt (see api/routes/chat.py) so she stays in character
    everywhere - chat, voice, CLI.
    """
    return {"text": memory.get_persona()}


@router.post("/persona")
def set_persona(payload: PersonaSet, memory: MemoryManager = Depends(get_memory_manager)):
    memory.set_persona(payload.text)
    return {"ok": True}


@router.post("/long-term")
def set_long_term(payload: LongTermSet, memory: MemoryManager = Depends(get_memory_manager)):
    memory.remember(payload.category, payload.key, payload.value)
    return {"ok": True}


@router.get("/long-term")
def list_long_term_categories(memory: MemoryManager = Depends(get_memory_manager)):
    return memory.list_long_term_categories()


@router.get("/long-term/{category}")
def get_long_term_category(category: str, memory: MemoryManager = Depends(get_memory_manager)):
    return memory.recall_category(category)


@router.post("/project")
def set_project(payload: ProjectSet, memory: MemoryManager = Depends(get_memory_manager)):
    memory.set_project_memory(payload.project, payload.key, payload.value)
    return {"ok": True}


@router.get("/project/{project}")
def get_project(project: str, memory: MemoryManager = Depends(get_memory_manager)):
    return memory.get_project_memory(project)


@router.get("/project")
def list_projects(memory: MemoryManager = Depends(get_memory_manager)):
    return memory.list_projects()


@router.get("/projects")
def list_projects_meta(memory: MemoryManager = Depends(get_memory_manager)):
    """Project list with entry counts and last-updated stamps for the Memory tab."""
    return memory.list_projects_meta()


@router.delete("/project/{project}")
def delete_project(project: str, memory: MemoryManager = Depends(get_memory_manager)):
    deleted = memory.delete_project(project)
    if not deleted:
        raise HTTPException(404, f"No memory found for project '{project}'")
    return {"ok": True, "deleted_entries": deleted}


@router.post("/project/rename")
def rename_project(payload: ProjectRename, memory: MemoryManager = Depends(get_memory_manager)):
    old, new = payload.old.strip(), payload.new.strip()
    if not old or not new:
        raise HTTPException(400, "Both old and new project names are required")
    if old == new:
        return {"ok": True}
    try:
        memory.rename_project(old, new)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@router.get("/active-project")
def get_active_project(memory: MemoryManager = Depends(get_memory_manager)):
    """The project whose memory chat injects as context. None = no project focus."""
    return {"project": memory.get_active_project()}


@router.post("/active-project")
def set_active_project(payload: ActiveProjectSet, memory: MemoryManager = Depends(get_memory_manager)):
    memory.set_active_project(payload.project)
    return {"ok": True, "project": memory.get_active_project()}


@router.get("/overview")
def memory_overview(memory: MemoryManager = Depends(get_memory_manager)):
    """Counts per memory tier - lets the Memory tab show what Emma is carrying."""
    return memory.overview()


@router.get("/sessions")
def list_sessions(memory: MemoryManager = Depends(get_memory_manager)):
    return memory.list_sessions()


@router.delete("/sessions/{session_id}")
def clear_session(session_id: str, memory: MemoryManager = Depends(get_memory_manager)):
    return {"ok": True, "deleted_turns": memory.clear_session(session_id)}


@router.post("/daily")
def set_daily(payload: DailySet, memory: MemoryManager = Depends(get_memory_manager)):
    memory.set_daily(payload.key, payload.value)
    return {"ok": True}


@router.get("/daily")
def get_daily(memory: MemoryManager = Depends(get_memory_manager)):
    return memory.get_daily()


class SaveFromChat(BaseModel):
    """
    Backs the chat window's "Save to memory" button. Lets the GUI write
    a highlighted exchange into long-term memory, project memory, or both
    in a single call instead of juggling three separate endpoints.
    """

    targets: list[str]  # any of: "long_term", "project"
    key: str
    value: str
    category: Optional[str] = None  # required if "long_term" in targets
    project: Optional[str] = None  # required if "project" in targets


@router.post("/save")
def save_from_chat(payload: SaveFromChat, memory: MemoryManager = Depends(get_memory_manager)):
    if not payload.targets:
        raise HTTPException(400, "No memory target given")

    saved = {}
    if "long_term" in payload.targets:
        if not payload.category:
            raise HTTPException(400, "category is required to save to long-term memory")
        memory.remember(payload.category, payload.key, payload.value)
        saved["long_term"] = True
    if "project" in payload.targets:
        if not payload.project:
            raise HTTPException(400, "project is required to save to project memory")
        memory.set_project_memory(payload.project, payload.key, payload.value)
        saved["project"] = True

    if not saved:
        raise HTTPException(400, f"Unknown target(s): {payload.targets}")
    return saved
