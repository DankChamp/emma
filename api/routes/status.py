from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_busy_mode_manager, get_notification_manager
from core.busy_mode import BusyModeManager
from core.notifications import NotificationManager

router = APIRouter(prefix="/status", tags=["status"])


class GoBusyRequest(BaseModel):
    note: Optional[str] = None


class ContactCreate(BaseModel):
    name: str
    busy_message: str
    free_message: Optional[str] = None


@router.get("")
def get_status(busy_mode: BusyModeManager = Depends(get_busy_mode_manager)):
    return busy_mode.get_state()


@router.post("/busy")
async def go_busy(
    payload: GoBusyRequest,
    busy_mode: BusyModeManager = Depends(get_busy_mode_manager),
    notifications: NotificationManager = Depends(get_notification_manager),
):
    state = await busy_mode.go_busy(payload.note)
    # Message the people you've assigned notify-on-busy, over Telegram.
    await notifications.broadcast_availability(is_busy=True, note=payload.note)
    return state


@router.post("/free")
async def go_free(
    busy_mode: BusyModeManager = Depends(get_busy_mode_manager),
    notifications: NotificationManager = Depends(get_notification_manager),
):
    state = await busy_mode.go_free()
    await notifications.broadcast_availability(is_busy=False)
    return state


@router.post("/contacts")
def add_contact(payload: ContactCreate, busy_mode: BusyModeManager = Depends(get_busy_mode_manager)):
    return busy_mode.add_contact(payload.name, payload.busy_message, payload.free_message)


@router.get("/contacts")
def list_contacts(busy_mode: BusyModeManager = Depends(get_busy_mode_manager)):
    return busy_mode.list_contacts()


@router.delete("/contacts/{name}")
def remove_contact(name: str, busy_mode: BusyModeManager = Depends(get_busy_mode_manager)):
    return {"removed": busy_mode.remove_contact(name)}
