"""
Emma - entry point.

Run with:
    uvicorn main:app --reload

This file assembles routers, owns the app lifespan, and constructs the
handful of *long-lived* singletons that must outlive a single request: the
scheduler, the Telegram bot, and the managers the bot + scheduler reach into.
Everything else stays per-request (see api/deps.py). No business logic here.
"""
import asyncio
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes import chat, memory, notifications, planning, reminders, schedule, status, selfcare, tasks
from api.routes import settings as settings_routes
from config import get_settings
from core.busy_mode import BusyModeManager
from core.notifications import NotificationManager, TelegramMessenger
from core.reminders import ReminderManager
from core.persistence import hf_backup
from core.router import AIRouter
from core.schedule import TimetableManager

logger = logging.getLogger("emma.main")

settings = get_settings()

# On ephemeral hosts (HF Spaces) pull the last data/*.db snapshot before any
# manager opens its database. No-op unless EMMA_HF_BACKUP_REPO + HF_TOKEN set.
hf_backup.restore()

scheduler = AsyncIOScheduler()

# ---- Long-lived singletons ----
busy_mode = BusyModeManager(settings.busy_mode_db_path)
telegram = TelegramMessenger(settings.telegram_bot_token or "")
notifications_mgr = NotificationManager(settings.notifications_db_path, telegram=telegram)
telegram._notify_mgr = notifications_mgr
telegram._busy_mgr = busy_mode

ai_router = AIRouter(settings)
timetable = TimetableManager(settings.schedule_db_path, ai_router=ai_router)
notifications_mgr.schedule = timetable

# Reminders: one manager + one recurring sweep instead of per-reminder jobs,
# so reminders created while Emma was off still fire after a restart.
reminders_mgr = ReminderManager(
    settings.reminders_db_path, notifications=notifications_mgr, busy_mode=busy_mode
)


async def _reminder_sweep() -> None:
    try:
        await reminders_mgr.check_due()
    except Exception as exc:  # noqa: BLE001 - a bad row must not kill the sweep
        logger.warning("Reminder sweep failed: %s", exc)


async def _send_block_notification(title: str, block_start: datetime) -> None:
    """Called when a scheduled block's start time arrives."""
    await notifications_mgr.notify_user(
        "champ", f"⏰ {title} starts now"
    )


def _schedule_block_notification(title: str, block_start: datetime) -> None:
    """Queue a Telegram notification for the start of a future block."""
    scheduler.add_job(
        _send_block_notification,
        'date',
        run_date=block_start,
        args=[title, block_start],
        id=f"block-{block_start.isoformat()}",
        replace_existing=True,
    )


timetable.set_block_notify_callback(_schedule_block_notification)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    scheduler.add_job(_reminder_sweep, "interval", seconds=30, id="reminder-sweep",
                      replace_existing=True)
    if hf_backup.enabled:
        scheduler.add_job(hf_backup.upload, "interval", minutes=10,
                          id="hf-backup", replace_existing=True)
    if settings.telegram_bot_token:
        try:
            await telegram.start()
        except Exception as exc:
            logger.warning("Telegram bot failed to auto-start: %s", exc)
    yield
    if telegram.is_running:
        await telegram.stop()
    scheduler.shutdown()
    # Final snapshot; force=True waits out any in-flight periodic upload
    # instead of silently skipping (which would lose the last writes).
    await hf_backup.upload(force=True)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

app.include_router(schedule.router)
app.include_router(chat.router)
app.include_router(memory.router)
app.include_router(tasks.router)
app.include_router(reminders.router)
app.include_router(planning.router)
app.include_router(status.router)
app.include_router(settings_routes.router)
app.include_router(selfcare.router)
app.include_router(notifications.router)

WEB_DIR = Path(__file__).resolve().parent / "web"
if WEB_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")


@app.get("/")
def root():
    return {"status": "Emma is running", "app": settings.app_name}


@app.get("/health")
def health():
    return {"ok": True}
