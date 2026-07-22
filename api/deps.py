"""
Dependency injection wiring.

FastAPI's Depends() calls these functions per-request. Cheap, stateless
managers (memory) are still built per-request. But anything that must
be long-lived - the Telegram bot, the busy-mode state, the scheduler,
the timetable - is a singleton constructed once in main.py and simply handed
back here. That's why several of these functions do a lazy `from main import`:
it returns the shared instance instead of building a fresh one.

Important: `settings` must be injected via Depends(get_settings), never as
a bare default (`= None`) - FastAPI otherwise mistakes a bare Pydantic
model parameter for a request body field and starts expecting it in the
JSON payload.
"""
from fastapi import Depends

from config import Settings, get_settings
from core.busy_mode import BusyModeManager
from core.memory import MemoryManager
from core.notifications import AppointmentManager, NotificationManager, TelegramMessenger
from core.router import AIRouter
from core.schedule import TimetableManager
from core.selfcare import DiagnosticsManager, UpdateManager
from core.tasks import TaskManager
from core.profile import ProfileManager
from core.reminders import ReminderManager
from core.tasks.project_manager import ProjectManager


def get_profile_manager(settings: Settings = Depends(get_settings)) -> ProfileManager:
    return ProfileManager(settings.profile_db_path)


def get_project_manager(settings: Settings = Depends(get_settings)) -> ProjectManager:
    return ProjectManager(settings.tasks_db_path)


def get_memory_manager(settings: Settings = Depends(get_settings)) -> MemoryManager:
    return MemoryManager(settings.memory_db_path)


def get_task_manager(settings: Settings = Depends(get_settings)) -> TaskManager:
    return TaskManager(settings.tasks_db_path)


def get_reminder_manager() -> ReminderManager:
    from main import reminders_mgr  # shared: wired to Telegram + busy mode + the sweep
    return reminders_mgr


def get_busy_mode_manager() -> BusyModeManager:
    from main import busy_mode  # the shared singleton
    return busy_mode


def get_ai_router() -> AIRouter:
    from main import ai_router
    return ai_router


def get_timetable_manager() -> TimetableManager:
    from main import timetable  # shared: wired to the AI router
    return timetable


def get_telegram_messenger() -> TelegramMessenger:
    from main import telegram
    return telegram


def get_notification_manager() -> NotificationManager:
    from main import notifications_mgr  # shared: owns the live bot + people table
    return notifications_mgr


def get_diagnostics_manager(settings: Settings = Depends(get_settings)) -> DiagnosticsManager:
    from config import BASE_DIR
    return DiagnosticsManager(BASE_DIR)


def get_appointment_manager(settings: Settings = Depends(get_settings)) -> AppointmentManager:
    return AppointmentManager(settings.appointments_db_path)


def get_update_manager(settings: Settings = Depends(get_settings)) -> UpdateManager:
    from config import BASE_DIR
    return UpdateManager(BASE_DIR)
