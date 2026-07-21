"""
Schedule / Planner routes — the daily timetable that powers the Planner tab
and the bot's "when am I free" answers.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_memory_manager, get_timetable_manager
from core.memory import MemoryManager
from core.schedule import TimetableManager

router = APIRouter(prefix="/schedule", tags=["schedule"])


class Block(BaseModel):
    start: str          # "HH:MM"
    end: str            # "HH:MM"
    title: str
    busy: bool = True


class SaveScheduleRequest(BaseModel):
    day: str = "today"
    blocks: list[Block]


class BuildScheduleRequest(BaseModel):
    day: str = "today"
    text: str


class DeleteDayRequest(BaseModel):
    day: str = "today"


def _parse_day(day: str) -> date:
    if not day or day == "today":
        return date.today()
    try:
        return date.fromisoformat(day)
    except ValueError:
        return date.today()


@router.get("/free-hint")
def free_hint(timetable: TimetableManager = Depends(get_timetable_manager)):
    now = datetime.now()
    return {"hint": timetable.free_hint(now), "next_free": timetable.next_free(now)}


@router.get("/{day}")
def get_day(day: str = "today", timetable: TimetableManager = Depends(get_timetable_manager)):
    return timetable.list_day(_parse_day(day))


@router.post("")
def save_day(payload: SaveScheduleRequest, timetable: TimetableManager = Depends(get_timetable_manager)):
    blocks = [b.model_dump() for b in payload.blocks]
    return timetable.set_day(_parse_day(payload.day), blocks, create_reminders=True)


@router.post("/build")
async def build_day(
    payload: BuildScheduleRequest,
    timetable: TimetableManager = Depends(get_timetable_manager),
    memory: MemoryManager = Depends(get_memory_manager),
):
    context_parts = []
    lt = memory.get_long_term_text()
    if lt.strip():
        context_parts.append(f"Long-term context:\n{lt}")
    dt = memory.get_daily_text()
    if dt.strip():
        context_parts.append(f"Today's notes:\n{dt}")
    projects = memory.list_projects()
    if projects:
        context_parts.append(f"Active projects: {', '.join(projects)}")
    memory_context = "\n\n".join(context_parts) if context_parts else None

    return await timetable.build_from_text(
        payload.text, _parse_day(payload.day), create_reminders=True, memory_context=memory_context
    )


@router.post("/delete")
def delete_day(payload: DeleteDayRequest, timetable: TimetableManager = Depends(get_timetable_manager)):
    timetable.delete_day(_parse_day(payload.day))
    return {"ok": True}
