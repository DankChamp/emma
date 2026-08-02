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
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes import appointments, chat, memory, notifications, planning, profile, projects, reminders, schedule, status, selfcare, tasks, facts, ingest
from api.routes import settings as settings_routes
from api.routes import aqua as aqua_routes
from api.routes import luna as luna_routes
from config import get_settings
from core.busy_mode import BusyModeManager
from core.notifications import AppointmentManager, NotificationManager, TelegramMessenger
from core.profile.manager import ProfileManager
from core.reminders import ReminderManager
from core.tasks.manager import TaskManager
from core.timeutil import local_now, local_today
from core.persistence import hf_backup
from core.router import AIRouter
from core.schedule import TimetableManager
from core.tools.registry import get_registry, Tool

logger = logging.getLogger("emma.main")

settings = get_settings()

# On ephemeral hosts (HF Spaces) pull the last data/*.db snapshot before any
# manager opens its database. No-op unless EMMA_HF_BACKUP_REPO + HF_TOKEN set.
hf_backup.restore()

TZ = ZoneInfo(settings.tz)

def _local_to_utc(dt: datetime) -> datetime:
    """Convert a local naive datetime to UTC naive for APScheduler."""
    return dt.replace(tzinfo=TZ).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

scheduler = AsyncIOScheduler()

# ---- Long-lived singletons ----
busy_mode = BusyModeManager(settings.busy_mode_db_path)
ai_router = AIRouter(settings)
profile_mgr = ProfileManager(settings.profile_db_path)
task_mgr = TaskManager(settings.tasks_db_path)

telegram = TelegramMessenger(
    bot_token=settings.telegram_bot_token or "",
    owner_name=settings.owner_name,
    owner_telegram_id=settings.owner_telegram_id,
    ai_router=ai_router,
    profile_mgr=profile_mgr,
    task_mgr=task_mgr,
)
notifications_mgr = NotificationManager(settings.notifications_db_path, telegram=telegram)
appointment_mgr = AppointmentManager(settings.appointments_db_path)
telegram._notify_mgr = notifications_mgr
telegram._busy_mgr = busy_mode
telegram._appointment_mgr = appointment_mgr

timetable = TimetableManager(settings.schedule_db_path, ai_router=ai_router)
notifications_mgr.schedule = timetable
telegram._timetable_mgr = timetable

# Reminders: one manager + one recurring sweep instead of per-reminder jobs,
# so reminders created while Emma was off still fire after a restart.
reminders_mgr = ReminderManager(
    settings.reminders_db_path, notifications=notifications_mgr, busy_mode=busy_mode
)

# ---- Tool registry (used by Aqua, Luna, and standalone tools) ----
reg = get_registry()

# ---- Aqua lifecycle manager ----
aqua_mgr = None
if settings.aqua_project_dir:
    from core.aqua import AquaManager
    aqua_mgr = AquaManager(settings.aqua_project_dir, settings.aqua_api_url, settings.aqua_api_key)

# ---- Register Aqua tools ----
if aqua_mgr:
    from core.aqua import AquaClient

    _aqua_client = AquaClient(settings.aqua_api_url, settings.aqua_api_key)

    async def _aqua_chat(**kw):
        r = await _aqua_client.chat(**kw)
        return r or "no response"
    reg.register(Tool("aqua_ask", "Ask Aqua (research/study AI) a question", [
        ("message", "The question to ask", True),
        ("task_type", "Task type: research, study, conversation (default: research)", False),
    ], _aqua_chat))

    async def _aqua_create_doc(**kw):
        r = await _aqua_client.create_document(**kw)
        return f"Document created: {r.get('title', 'unknown')} (id={r.get('id', '?')})" if r else "failed"
    reg.register(Tool("aqua_create_document", "Create a research document in Aqua", [
        ("title", "Document title", True),
        ("content", "Document content", True),
        ("authors", "Author(s)", False),
        ("source", "Source type (manual, pdf, url)", False),
        ("tags", "Comma-separated tags as a list like [tag1, tag2]", False),
    ], _aqua_create_doc))

    async def _aqua_search(**kw):
        results = await _aqua_client.search_documents(**kw)
        if not results:
            return "No results found"
        parts = [f"{r.get('title', 'Untitled')}: {r.get('content', '')[:200]}" for r in results[:3]]
        return "\n".join(parts)
    reg.register(Tool("aqua_search", "Search Aqua's document knowledge base", [
        ("query", "Search query", True),
        ("limit", "Max results (default: 5)", False),
    ], _aqua_search))

    async def _aqua_create_note(**kw):
        r = await _aqua_client.create_note(**kw)
        return f"Note created: {r.get('title', 'untitled')} (id={r.get('id', '?')})" if r else "failed"
    reg.register(Tool("aqua_create_note", "Create a research note in Aqua", [
        ("content", "Note content", True),
        ("title", "Note title", False),
    ], _aqua_create_note))

    async def _aqua_create_fc(**kw):
        r = await _aqua_client.create_flashcard(**kw)
        return f"Flashcard created: {r.get('question', '?')} (id={r.get('id', '?')})" if r else "failed"
    reg.register(Tool("aqua_create_flashcard", "Create a flashcard in Aqua", [
        ("question", "The question", True),
        ("answer", "The answer", True),
        ("topic", "Topic category", False),
    ], _aqua_create_fc))

    async def _aqua_launch(**kw):
        if aqua_mgr:
            ok = await aqua_mgr.launch()
            return "Aqua launched" if ok else "Failed to launch Aqua"
        return "Aqua manager not configured"
    reg.register(Tool("aqua_launch", "Launch the Aqua server if not running", [], _aqua_launch))

    async def _aqua_stop(**kw):
        if aqua_mgr:
            aqua_mgr.stop()
            return "Aqua stopped"
        return "Aqua manager not configured"
    reg.register(Tool("aqua_stop", "Stop the Aqua server", [], _aqua_stop))

    async def _aqua_status(**kw):
        if aqua_mgr:
            h = await aqua_mgr.health()
            return f"Running: {h['running']} | Alive: {h['alive']} | PID: {h['pid']} | Uptime: {h['uptime']}s"
        return "Aqua manager not configured"
    reg.register(Tool("aqua_status", "Check Aqua's server status", [], _aqua_status))


# ---- Luna lifecycle manager ----
luna_mgr = None
if settings.luna_project_dir:
    from core.luna import LunaManager
    luna_mgr = LunaManager(settings.luna_project_dir, settings.luna_api_url, settings.luna_api_key)

# ---- Register Luna tools ----
if luna_mgr:
    from core.luna import LunaClient

    _luna_client = LunaClient(settings.luna_api_url, settings.luna_api_key)

    async def _luna_chat(**kw):
        r = await _luna_client.chat(**kw)
        return r or "no response"
    reg.register(Tool("luna_ask", "Ask Luna (coding AI) to write, edit, or debug code", [
        ("message", "The coding task to ask about", True),
    ], _luna_chat))

    async def _luna_status(**kw):
        if luna_mgr:
            h = await luna_mgr.health()
            return f"Running: {h['running']} | Alive: {h['alive']} | PID: {h['pid']} | Uptime: {h['uptime']}s"
        return "Luna manager not configured"
    reg.register(Tool("luna_status", "Check Luna's server status", [], _luna_status))

    async def _luna_launch(**kw):
        if luna_mgr:
            ok = await luna_mgr.launch()
            return "Luna launched" if ok else "Failed to launch Luna"
        return "Luna manager not configured"
    reg.register(Tool("luna_launch", "Launch the Luna server if not running", [], _luna_launch))

    async def _luna_stop(**kw):
        if luna_mgr:
            luna_mgr.stop()
            return "Luna stopped"
        return "Luna manager not configured"
    reg.register(Tool("luna_stop", "Stop the Luna server", [], _luna_stop))

# ---- Register share_fact tool (let Emma's AI push user data to subordinates) ----
async def _share_fact(**kw):
    from core.facts import FactRouter

    fact = kw.get("fact", "")
    if not fact:
        return "No fact provided"
    router = FactRouter(settings)
    result = await router.push_fact(fact)
    pushed = result.get("pushed_to", [])
    if pushed:
        return f"Shared fact with: {', '.join(pushed)}"
    return "Fact noted but no subordinates were reachable to share with"
reg.register(Tool("share_fact", "Share a fact about the user with subordinate AIs (Aqua, Luna)", [
    ("fact", "The fact about the user to share", True),
], _share_fact))

# ---- Luna context pull tool ----
async def _luna_pull_context(**kw):
    from core.luna.client import LunaClient
    from core.memory import MemoryManager
    from config import DATA_DIR

    client = LunaClient(settings.luna_api_url, settings.luna_api_key)
    history = await client.get_history()
    if not history:
        return "Could not pull context from Luna (not reachable or no history)"
    msgs = history.get("messages", [])
    if not msgs:
        return "Luna has no recent history to pull"
    lines = [f"{m['role']}: {m['content']}" for m in msgs]
    context = "Luna's recent coding sessions:\n" + "\n".join(lines)
    memory = MemoryManager(settings.memory_db_path)
    existing = memory.get_project_text("luna-coding")
    updated = (existing + "\n---\n" + context) if existing else context
    memory.set_project_text("luna-coding", updated)
    return f"Pulled {len(msgs)} messages from Luna into project memory 'luna-coding'"
reg.register(Tool("luna_pull_context", "Pull Luna's recent coding session context into Emma's memory", [], _luna_pull_context))


async def _reminder_sweep() -> None:
    try:
        await reminders_mgr.check_due()
    except Exception as exc:  # noqa: BLE001 - a bad row must not kill the sweep
        logger.warning("Reminder sweep failed: %s", exc)


AUTO_BUSY_PREFIX = "📅 "

async def _send_block_notification(title: str, block_start: datetime) -> None:
    """Called when a scheduled block's start time arrives."""
    state = busy_mode.get_state()
    if not state.is_busy:
        await busy_mode.go_busy(note=f"{AUTO_BUSY_PREFIX}{title}")
        await notifications_mgr.broadcast_availability(is_busy=True, note=title)
    t = block_start.strftime("%H:%M")
    sent = await notifications_mgr.notify_owner(
        f"⏰ {t} — {title}"
    )
    if not sent:
        logger.warning("Block notification not delivered — owner has no chat_id yet.")


def _schedule_block_notification(title: str, block_start: datetime) -> None:
    """Queue a Telegram notification for the start of a future block."""
    utc_start = _local_to_utc(block_start)
    if utc_start <= datetime.utcnow():
        # Never fire reminders for blocks that already started.
        logger.info("Skipping block notification '%s' at %s — already in the past.",
                    title, block_start.strftime("%H:%M"))
        return
    scheduler.add_job(
        _send_block_notification,
        'date',
        run_date=utc_start,
        args=[title, block_start],
        id=f"block-{block_start.isoformat()}",
        replace_existing=True,
    )


timetable.set_block_notify_callback(_schedule_block_notification)


async def _schedule_block_sweep() -> None:
    """Re-create block notification jobs + sync busy mode with timetable."""
    try:
        now = local_now()
        # --- Sync busy mode ---
        state = busy_mode.get_state()
        busy_block = timetable.current_busy_block(now)
        if busy_block and not state.is_busy:
            note = f"{AUTO_BUSY_PREFIX}{busy_block.title}"
            await busy_mode.go_busy(note=note)
            await notifications_mgr.broadcast_availability(is_busy=True, note=busy_block.title)
            logger.info("Auto busy: %s", busy_block.title)
        elif not busy_block and state.is_busy and state.note and state.note.startswith(AUTO_BUSY_PREFIX):
            await busy_mode.go_free()
            await notifications_mgr.broadcast_availability(is_busy=False)
            logger.info("Auto free — no busy block in schedule.")
        # --- Re-create notification jobs after restart ---
        for days_offset in (0, 1):
            day = local_today() + timedelta(days=days_offset)
            for block in timetable.list_day(day):
                if not block.busy or block.start <= now:
                    continue
                job_id = f"block-{block.start.isoformat()}"
                if scheduler.get_job(job_id):
                    continue
                utc_start = _local_to_utc(block.start)
                scheduler.add_job(
                    _send_block_notification,
                    'date',
                    run_date=utc_start,
                    args=[block.title, block.start],
                    id=job_id,
                    replace_existing=True,
                )
                logger.info("Re-created block notification: %s at %s", block.title, block.start.strftime("%H:%M"))
    except Exception as exc:
        logger.warning("Schedule block sweep failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    await _schedule_block_sweep()

    # Auto-launch Aqua if configured
    if settings.aqua_auto_launch and aqua_mgr:
        try:
            ok = await aqua_mgr.launch()
            if ok:
                logger.info("Aqua auto-launched")
        except Exception as exc:
            logger.warning("Aqua auto-launch failed: %s", exc)

    # Auto-launch Luna if configured
    if settings.luna_auto_launch and luna_mgr:
        try:
            ok = await luna_mgr.launch()
            if ok:
                logger.info("Luna auto-launched")
        except Exception as exc:
            logger.warning("Luna auto-launch failed: %s", exc)
    scheduler.add_job(_reminder_sweep, "interval", seconds=30, id="reminder-sweep",
                      replace_existing=True)
    scheduler.add_job(_schedule_block_sweep, "interval", minutes=5, id="schedule-block-sweep",
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
app.include_router(profile.router)
app.include_router(projects.router)
app.include_router(appointments.router)
app.include_router(reminders.router)
app.include_router(planning.router)
app.include_router(status.router)
app.include_router(settings_routes.router)
app.include_router(selfcare.router)
app.include_router(notifications.router)
app.include_router(aqua_routes.router)
app.include_router(luna_routes.router)
app.include_router(facts.router)
app.include_router(ingest.router)

WEB_DIR = Path(__file__).resolve().parent / "web"
if WEB_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(WEB_DIR), html=True), name="ui")


@app.get("/")
def root():
    return {"status": "Emma is running", "app": settings.app_name}


@app.get("/health")
async def health():
    aqua_status = {"configured": aqua_mgr is not None}
    if aqua_mgr:
        try:
            h = await aqua_mgr.health()
            aqua_status["running"] = h["running"]
            aqua_status["alive"] = h["alive"]
        except Exception:
            aqua_status["running"] = False
            aqua_status["alive"] = False
    luna_status = {"configured": luna_mgr is not None}
    if luna_mgr:
        try:
            h = await luna_mgr.health()
            luna_status["running"] = h["running"]
            luna_status["alive"] = h["alive"]
        except Exception:
            luna_status["running"] = False
            luna_status["alive"] = False
    return {
        "ok": True,
        "auth_required": bool(settings.web_password),
        "telegram": {
            "configured": bool(settings.telegram_bot_token),
            "running": telegram.is_running,
            "error": telegram.error,
        },
        "aqua": aqua_status,
        "luna": luna_status,
    }


# ---- Auth middleware (protects API + UI) ----
from pydantic import BaseModel

class LoginRequest(BaseModel):
    password: str

@app.post("/api/auth")
def login(payload: LoginRequest):
    logger.info("login attempt — password set=%s", bool(settings.web_password))
    if settings.web_password and payload.password == settings.web_password:
        return {"ok": True}
    logger.warning("login failed — wrong password")
    raise HTTPException(401, "Wrong password")

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not settings.web_password:
        return await call_next(request)
    # Public paths
    if request.url.path in ("/", "/health", "/api/auth") or request.url.path.startswith("/ui/"):
        return await call_next(request)
    # Check Authorization header
    auth = request.headers.get("Authorization", "")
    if auth == f"Bearer {settings.web_password}":
        return await call_next(request)
    # Check cookie (set by frontend after login)
    if request.cookies.get("emma_token") == settings.web_password:
        return await call_next(request)
    return JSONResponse({"detail": "Unauthorized"}, status_code=401)
