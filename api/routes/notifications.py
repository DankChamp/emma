from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_notification_manager
from core.notifications import NotificationManager

router = APIRouter(prefix="/notifications", tags=["notifications"])


class LabelUserRequest(BaseModel):
    telegram_id: int
    label: str
    role: str


class SetChatIdRequest(BaseModel):
    telegram_id: int
    chat_id: int


class SetPriorityRequest(BaseModel):
    telegram_id: int
    priority: str  # "high" | "normal" | "low"


class SetNotifyRequest(BaseModel):
    telegram_id: int
    notify_on_busy: bool


class SetMessagesRequest(BaseModel):
    telegram_id: int
    busy_message: str | None = None
    free_message: str | None = None


class SetOwnerRequest(BaseModel):
    telegram_id: int


class SetPromptRequest(BaseModel):
    telegram_id: int
    prompt: str | None = None


@router.get("/bot-status")
def bot_status(notifications: NotificationManager = Depends(get_notification_manager)):
    return notifications.bot_status


@router.post("/bot/start")
async def start_bot(notifications: NotificationManager = Depends(get_notification_manager)):
    await notifications.start_bot()
    return {"ok": True, "status": notifications.bot_status}


@router.post("/bot/stop")
async def stop_bot(notifications: NotificationManager = Depends(get_notification_manager)):
    await notifications.stop_bot()
    return {"ok": True, "status": notifications.bot_status}


@router.get("/users")
def list_users(notifications: NotificationManager = Depends(get_notification_manager)):
    return notifications.list_users()


@router.post("/users/label")
def label_user(payload: LabelUserRequest, notifications: NotificationManager = Depends(get_notification_manager)):
    notifications.set_label(payload.telegram_id, payload.label, payload.role)
    return {"ok": True}


@router.post("/users/chat-id")
def set_chat_id(payload: SetChatIdRequest, notifications: NotificationManager = Depends(get_notification_manager)):
    notifications.set_chat_id(payload.telegram_id, payload.chat_id)
    return {"ok": True}


@router.post("/users/priority")
def set_priority(payload: SetPriorityRequest, notifications: NotificationManager = Depends(get_notification_manager)):
    notifications.set_priority(payload.telegram_id, payload.priority)
    return {"ok": True}


@router.post("/users/notify")
def set_notify(payload: SetNotifyRequest, notifications: NotificationManager = Depends(get_notification_manager)):
    notifications.set_notify_on_busy(payload.telegram_id, payload.notify_on_busy)
    return {"ok": True}


@router.post("/users/messages")
def set_messages(payload: SetMessagesRequest, notifications: NotificationManager = Depends(get_notification_manager)):
    notifications.set_custom_messages(payload.telegram_id, payload.busy_message, payload.free_message)
    return {"ok": True}


@router.post("/users/owner")
def set_owner(payload: SetOwnerRequest, notifications: NotificationManager = Depends(get_notification_manager)):
    notifications.set_owner(payload.telegram_id)
    return {"ok": True}


@router.post("/users/prompt")
def set_prompt(payload: SetPromptRequest, notifications: NotificationManager = Depends(get_notification_manager)):
    notifications.set_prompt(payload.telegram_id, payload.prompt)
    return {"ok": True}


@router.get("/messages")
def get_messages(notifications: NotificationManager = Depends(get_notification_manager)):
    return notifications.get_message_log()