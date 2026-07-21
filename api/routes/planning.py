"""
Daily planning - the spec's morning brief and night review.

Morning: today's tasks, deadlines, schedule, and AI-suggested priorities.
Night:   what got done, what rolls over, and a short AI summary of the day.

Both degrade gracefully: the structured data (tasks, schedule) is always
returned even when no AI provider is reachable - the `summary` field is just
empty then.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends

from api.deps import get_ai_router, get_memory_manager, get_task_manager, get_timetable_manager
from core.memory import MemoryManager
from core.router import AIRouter, TaskType
from core.schedule import TimetableManager
from core.tasks import TaskManager

router = APIRouter(prefix="/planning", tags=["planning"])


def _schedule_lines(timetable: TimetableManager) -> list[str]:
    return [
        f"{b.start.strftime('%H:%M')}-{b.end.strftime('%H:%M')} {b.title}"
        for b in timetable.list_day(date.today())
    ]


async def _ai_summary(ai_router: AIRouter, prompt: str) -> str:
    try:
        result = await ai_router.run(TaskType.GENERAL_ASSISTANT, prompt)
        return result.text.strip()
    except Exception:  # noqa: BLE001 - no provider up; brief still works without AI
        return ""


@router.get("/morning")
async def morning_brief(
    tasks: TaskManager = Depends(get_task_manager),
    timetable: TimetableManager = Depends(get_timetable_manager),
    memory: MemoryManager = Depends(get_memory_manager),
    ai_router: AIRouter = Depends(get_ai_router),
):
    pending = tasks.list(status="pending")
    counts = tasks.counts()
    schedule = _schedule_lines(timetable)
    daily_note = memory.get_daily_text()

    summary = ""
    if pending or schedule:
        task_text = tasks.pending_summary() or "(no open tasks)"
        prompt = (
            "You are Emma, a personal assistant, giving your owner a short morning brief. "
            "Given their open tasks and today's schedule, write 3-5 sentences: greet them, "
            "estimate today's workload, and suggest which 1-3 tasks to hit first and why. "
            "Be warm but concise. No markdown headers.\n\n"
            f"Date: {date.today().isoformat()}\n"
            f"Open tasks:\n{task_text}\n\n"
            f"Today's schedule:\n" + ("\n".join(schedule) or "(empty)") +
            (f"\n\nToday's note:\n{daily_note}" if daily_note else "")
        )
        summary = await _ai_summary(ai_router, prompt)

    return {
        "date": date.today().isoformat(),
        "tasks": pending,
        "counts": counts,
        "schedule": schedule,
        "daily_note": daily_note,
        "summary": summary,
    }


@router.get("/night")
async def night_review(
    tasks: TaskManager = Depends(get_task_manager),
    ai_router: AIRouter = Depends(get_ai_router),
):
    done_today = tasks.completed_today()
    remaining = tasks.list(status="pending")

    summary = ""
    if done_today or remaining:
        done_text = "\n".join(f"- {t['title']}" for t in done_today) or "(nothing marked done)"
        left_text = tasks.pending_summary() or "(all clear)"
        prompt = (
            "You are Emma, a personal assistant, reviewing your owner's day with them. "
            "In 3-4 sentences: acknowledge what they finished, note what rolls over to "
            "tomorrow, and end on an encouraging note. Be genuine, not saccharine.\n\n"
            f"Completed today:\n{done_text}\n\nStill open:\n{left_text}"
        )
        summary = await _ai_summary(ai_router, prompt)

    return {
        "date": date.today().isoformat(),
        "completed_today": done_today,
        "remaining": remaining,
        "summary": summary,
        "generated_at": datetime.now().isoformat(),
    }
