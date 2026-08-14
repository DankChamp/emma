from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_appointment_manager, get_timetable_manager
from core.notifications import AppointmentManager
from core.schedule import TimetableManager

router = APIRouter(prefix="/appointments", tags=["appointments"])


def _parse_day(day: str) -> date:
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, f"'{day}' is not a valid date (expected YYYY-MM-DD)")


class AppointmentCreate(BaseModel):
    person_label: str
    person_telegram_id: Optional[int] = None
    day: str
    start: str
    end: str
    title: str = ""
    note: str = ""


@router.get("")
def list_appointments(status: Optional[str] = None, day: Optional[str] = None,
                      am: AppointmentManager = Depends(get_appointment_manager)):
    d = _parse_day(day) if day else None
    return am.list(status=status, day=d)


@router.post("")
def create_appointment(payload: AppointmentCreate,
                       am: AppointmentManager = Depends(get_appointment_manager)):
    d = _parse_day(payload.day)
    return am.create(payload.person_label, payload.person_telegram_id,
                     d, payload.start, payload.end,
                     title=payload.title, note=payload.note)


@router.get("/free-slots")
def free_slots(day: str,
               am: AppointmentManager = Depends(get_appointment_manager),
               timetable: TimetableManager = Depends(get_timetable_manager)):
    d = _parse_day(day)
    blocks = timetable.list_day(d)
    return am.find_free_slots(d, blocks)


@router.post("/{appointment_id}/confirm")
def confirm_appointment(appointment_id: int,
                        am: AppointmentManager = Depends(get_appointment_manager)):
    a = am.confirm(appointment_id)
    if not a:
        raise HTTPException(404, "Appointment not found")
    return a


@router.post("/{appointment_id}/reject")
def reject_appointment(appointment_id: int,
                       am: AppointmentManager = Depends(get_appointment_manager)):
    a = am.reject(appointment_id)
    if not a:
        raise HTTPException(404, "Appointment not found")
    return a


@router.delete("/{appointment_id}")
def delete_appointment(appointment_id: int,
                       am: AppointmentManager = Depends(get_appointment_manager)):
    if not am.delete(appointment_id):
        raise HTTPException(404, "Appointment not found")
    return {"ok": True}
