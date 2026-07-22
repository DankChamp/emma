"""
Schedule / Planner routes — the daily timetable that powers the Planner tab
and the bot's "when am I free" answers.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_appointment_manager, get_memory_manager, get_notification_manager, get_profile_manager, get_project_manager, get_task_manager, get_timetable_manager
from core.memory import MemoryManager
from core.notifications import AppointmentManager, NotificationManager
from core.profile import ProfileManager
from core.schedule import TimetableManager
from core.tasks import TaskManager
from core.tasks.project_manager import ProjectManager

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


class Build3DayRequest(BaseModel):
    text: str
    include_tasks: bool = True
    include_study: bool = True


@router.post("/build-3day")
async def build_3day(
    payload: Build3DayRequest,
    timetable: TimetableManager = Depends(get_timetable_manager),
    memory: MemoryManager = Depends(get_memory_manager),
    profile: ProfileManager = Depends(get_profile_manager),
    tasks: TaskManager = Depends(get_task_manager),
    pm: ProjectManager = Depends(get_project_manager),
    am: AppointmentManager = Depends(get_appointment_manager),
    nm: NotificationManager = Depends(get_notification_manager),
):
    profile_data = profile.get_all()
    pending_tasks = tasks.list(status="pending") if payload.include_tasks else None
    study_summary = pm.study_summary(days=7) if payload.include_study else None

    contacts = nm.list_users()
    contacts_text = ""
    if contacts:
        lines = ["People to talk to:"]
        for c in contacts:
            lines.append(f"- {c['label']} ({c['role']}, priority: {c['priority']})")
        contacts_text = "\n".join(lines)

    pending_appointments = am.list(status="pending")
    appointments_text = ""
    if pending_appointments:
        lines = ["Pending appointments:"]
        for a in pending_appointments:
            lines.append(f"- {a['person_label']}: {a['day']} {a['start']}-{a['end']} ({a['title']})")
        appointments_text = "\n".join(lines)

    context_parts = []
    lt = memory.get_long_term_text()
    if lt.strip():
        context_parts.append(f"Long-term context:\n{lt}")
    dt = memory.get_daily_text()
    if dt.strip():
        context_parts.append(f"Notes:\n{dt}")
    memory_context = "\n\n".join(context_parts) if context_parts else None

    return await timetable.build_multi_day(
        payload.text, days=3,
        profile=profile_data,
        pending_tasks=pending_tasks,
        study_summary=study_summary,
        contacts_text=contacts_text,
        appointments_text=appointments_text,
        memory_context=memory_context,
    )


@router.post("/delete")
def delete_day(payload: DeleteDayRequest, timetable: TimetableManager = Depends(get_timetable_manager)):
    timetable.delete_day(_parse_day(payload.day))
    return {"ok": True}


class EmergencyRequest(BaseModel):
    title: str
    duration_minutes: int = 60


@router.post("/emergency")
async def handle_emergency(payload: EmergencyRequest,
                           timetable: TimetableManager = Depends(get_timetable_manager)):
    return await timetable.handle_emergency(payload.title, payload.duration_minutes)
