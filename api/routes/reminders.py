from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_reminder_manager
from core.reminders import ReminderManager

router = APIRouter(prefix="/reminders", tags=["reminders"])


class ReminderCreate(BaseModel):
    message: str
    # Either an absolute time...
    trigger_at: Optional[datetime] = None
    # ...or a duration from now ("remind me in 20 minutes").
    in_minutes: Optional[int] = None
    repeat: str = "none"  # none | daily | weekly
    persistent: bool = False  # keep nagging until dismissed
    important: bool = False  # delivered even in busy mode


@router.get("")
def list_reminders(include_dismissed: bool = False,
                   reminders: ReminderManager = Depends(get_reminder_manager)):
    return reminders.list(include_dismissed=include_dismissed)


@router.post("")
def create_reminder(payload: ReminderCreate,
                    reminders: ReminderManager = Depends(get_reminder_manager)):
    if not payload.message.strip():
        raise HTTPException(400, "Reminder message is required")
    if payload.trigger_at is not None:
        trigger = payload.trigger_at
    elif payload.in_minutes is not None:
        if payload.in_minutes <= 0:
            raise HTTPException(400, "in_minutes must be positive")
        trigger = datetime.now() + timedelta(minutes=payload.in_minutes)
    else:
        raise HTTPException(400, "Provide trigger_at or in_minutes")
    return reminders.create(payload.message, trigger, repeat=payload.repeat,
                            persistent=payload.persistent, important=payload.important)


@router.post("/{reminder_id}/dismiss")
def dismiss_reminder(reminder_id: int,
                     reminders: ReminderManager = Depends(get_reminder_manager)):
    if not reminders.dismiss(reminder_id):
        raise HTTPException(404, f"No reminder with id {reminder_id}")
    return {"ok": True}


@router.delete("/{reminder_id}")
def delete_reminder(reminder_id: int,
                    reminders: ReminderManager = Depends(get_reminder_manager)):
    if not reminders.delete(reminder_id):
        raise HTTPException(404, f"No reminder with id {reminder_id}")
    return {"ok": True}
