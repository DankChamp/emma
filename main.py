"""
Emma - entry point.

Run with:
    uvicorn main:app --reload

This file assembles routers, owns the app lifespan, and constructs the
handful of *long-lived* singletons that must outlive a single request: the
scheduler, the Telegram bot, and the managers the bot + scheduler reach into.
Everything else stays per-request (see api/deps.py). No business logic here.
"""
import logging
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
from passlib.context import CryptContext

from api.routes import appointments, chat, memory, notifications, planning, profile, projects, reminders, schedule, status, selfcare, tasks, facts, ingest
from api.routes import settings as settings_routes
from api.routes import aqua as aqua_routes
from api.routes import luna as luna_routes
from config import get_settings
from core.busy_mode import BusyModeManager
from core.memory import MemoryManager
from core.notifications import AppointmentManager, NotificationManager, TelegramMessenger
from core.profile.manager import ProfileManager
from core.reminders import ReminderManager
from core.tasks.manager import TaskManager
from core.tasks.project_manager import ProjectManager
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

# ---- Auth Configuration ----
# JWT secret for session tokens (rotated on startup if not set in env)
JWT_SECRET = os.environ.get("EMMA_JWT_SECRET") or secrets.token_urlsafe(32)
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 24 * 30  # 30 days

# Password hashing for web_password - use argon2 if available, fallback to PBKDF2
try:
    from passlib.context import CryptContext
    pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
    _HAS_PASSLIB = True
except Exception:
    import hashlib
    import hmac
    _HAS_PASSLIB = False
    pwd_context = None

def _local_to_utc(dt: datetime) -> datetime:
    """Convert a local naive datetime to UTC naive for APScheduler."""
    return dt.replace(tzinfo=TZ).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _utc_now_naive() -> datetime:
    """UTC timestamp kept naive because APScheduler jobs here use naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None)

scheduler = AsyncIOScheduler()

# ---- Long-lived singletons ----
busy_mode = BusyModeManager(settings.busy_mode_db_path)
ai_router = AIRouter(settings)


def rebuild_ai_router() -> AIRouter:
    """
    Rebuild the provider stack from the current .env so saved settings take
    effect without a restart. Reassigns the module-level `ai_router`, which
    every consumer picks up via `from main import ai_router`, and re-points
    the singletons that captured the old router (Telegram, the timetable).
    """
    global ai_router, settings
    settings = get_settings()
    ai_router = AIRouter(settings)
    telegram._ai_router = ai_router
    timetable.ai_router = ai_router
    return ai_router

profile_mgr = ProfileManager(settings.profile_db_path)
task_mgr = TaskManager(settings.tasks_db_path)
memory_mgr = MemoryManager(settings.memory_db_path)
project_mgr = ProjectManager(settings.tasks_db_path)

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


AUTO_BUSY_PREFIX = "���� "

async def _send_block_notification(title: str, block_start: datetime) -> None:
    """Called when a scheduled block's start time arrives."""
    state = busy_mode.get_state()
    if not state.is_busy:
        await busy_mode.go_busy(note=f"{AUTO_BUSY_PREFIX}{title}")
        await notifications_mgr.broadcast_availability(is_busy=True, note=title)
    t = block_start.strftime("%H:%M")
    sent = await notifications_mgr.notify_owner(
        f"��� {t} — {title}"
    )
    if not sent:
        logger.warning("Block notification not delivered — owner has no chat_id yet.")


def _schedule_block_notification(title: str, block_start: datetime) -> None:
    """Queue a Telegram notification for the start of a future block."""
    utc_start = _local_to_utc(block_start)
    if utc_start <= _utc_now_naive():
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


def _auto_build_utc_cron() -> tuple[int, int]:
    """Convert settings.auto_build_time (local HH:MM) to the UTC hour/minute
    the APScheduler (which ticks on the server's UTC clock) should fire at."""
    try:
        hh, mm = (int(x) for x in settings.auto_build_time.split(":"))
    except (ValueError, AttributeError):
        hh, mm = 6, 0
    today = local_today()
    local_dt = datetime(today.year, today.month, today.day, hh, mm)
    utc_dt = local_dt.replace(tzinfo=TZ).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return utc_dt.hour, utc_dt.minute


async def _auto_build_daily_schedule() -> None:
    """Fill today's timetable with a fresh AI-generated plan when it's empty.

    Runs every day (and once at startup) so the day is planned automatically
    without pressing the build button. Never overwrites a manual schedule.
    """
    try:
        day = local_today()
        if timetable.list_day(day):
            logger.info("Auto-build: today already scheduled — skipping.")
            return

        # --- Context the generator can use to make it realistic ---
        profile_data = profile_mgr.get_all()
        try:
            pending_tasks = task_mgr.list(status="pending")
        except Exception:
            pending_tasks = None
        try:
            study_summary = project_mgr.study_summary(days=7)
        except Exception:
            study_summary = None

        contacts_text = None
        try:
            contacts = notifications_mgr.list_users()
            if contacts:
                lines = ["People to talk to:"]
                for c in contacts:
                    lines.append(f"- {c['label']} ({c['role']}, priority: {c['priority']})")
                contacts_text = "\n".join(lines)
        except Exception:
            contacts_text = None

        appointments_text = None
        try:
            pending_appts = appointment_mgr.list(status="pending")
            if pending_appts:
                lines = ["Pending appointments:"]
                for a in pending_appts:
                    lines.append(f"- {a['person_label']}: {a['day']} {a['start']}-{a['end']}")
                appointments_text = "\n".join(lines)
        except Exception:
            appointments_text = None

        memory_context = None
        try:
            parts = []
            lt = memory_mgr.get_long_term_text()
            if lt.strip():
                parts.append(f"Long-term context:\n{lt}")
            dt = memory_mgr.get_daily_text()
            if dt.strip():
                parts.append(f"Today's notes:\n{dt}")
            projects = memory_mgr.list_projects()
            if projects:
                parts.append(f"Active projects: {', '.join(projects)}")
            memory_context = "\n\n".join(parts) if parts else None
        except Exception:
            memory_context = None

        text = (
            "Plan a natural, well-balanced day: morning routine and breakfast, "
            "focused work/deep-work sessions, study, meals, short breaks, some "
            "movement or exercise, and time with my close contacts. Vary the "
            "order and timing from the usual pattern so each day feels fresh, "
            "but keep it realistic and achievable."
        )

        results = await timetable.build_multi_day(
            text=text, days=1,
            profile=profile_data,
            pending_tasks=pending_tasks,
            study_summary=study_summary,
            contacts_text=contacts_text,
            appointments_text=appointments_text,
            memory_context=memory_context,
        )
        blocks = results.get(day.isoformat(), [])
        if blocks:
            logger.info("Auto-built today's schedule: %d blocks", len(blocks))
            if settings.auto_build_notify:
                lines = []
                for b in blocks:
                    icon = "\U0001f534" if b.busy else "\U0001f7e2"
                    lines.append(f"{icon} {b.start:%H:%M}-{b.end:%H:%M} {b.title}")
                await notifications_mgr.notify_owner(
                    "\U0001f4c5 Today's schedule:\n" + "\n".join(lines)
                )
        else:
            logger.warning("Auto build returned no blocks for %s", day.isoformat())
    except Exception as exc:  # noqa: BLE001 - a failed build must not crash the app
        logger.warning("Auto schedule build failed: %s", exc)


# ---- Auth Helpers ----
def _hash_password(password: str) -> str:
    """Hash a password using argon2/bcrypt or fallback to PBKDF2-SHA256."""
    if _HAS_PASSLIB and pwd_context:
        return pwd_context.hash(password)
    # Fallback: PBKDF2-SHA256 with random salt
    import secrets
    salt = secrets.token_bytes(16)
    hash_val = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100000)
    return f"pbkdf2_sha256${salt.hex()}${hash_val.hex()}"

def _verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    if _HAS_PASSLIB and pwd_context:
        return pwd_context.verify(plain_password, hashed_password)
    # Fallback verification
    try:
        parts = hashed_password.split('$')
        if len(parts) == 3 and parts[0] == 'pbkdf2_sha256':
            salt = bytes.fromhex(parts[1])
            hash_val = bytes.fromhex(parts[2])
            computed = hashlib.pbkdf2_hmac('sha256', plain_password.encode(), salt, 100000)
            return hmac.compare_digest(computed, hash_val)
    except Exception:
        pass
    return False

def _create_session_token(data: dict) -> str:
    """Create a JWT session token."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(hours=JWT_EXPIRY_HOURS)
    to_encode.update({"exp": expire, "iat": datetime.now(UTC)})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def _decode_session_token(token: str) -> dict | None:
    """Decode and validate a JWT session token."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None

_web_password_hash: str | None = None

def _get_web_password_hash() -> str | None:
    """Get the hashed web password from settings."""
    global _web_password_hash
    if settings.web_password:
        if _web_password_hash is None:
            # Hash on first use if not already hashed
            if not settings.web_password.startswith("$2b$"):
                _web_password_hash = _hash_password(settings.web_password)
            else:
                _web_password_hash = settings.web_password
        return _web_password_hash
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not scheduler.running:
        scheduler.start()
    await _schedule_block_sweep()

    # Plan today automatically if it's still empty, then keep it daily.
    if settings.auto_build_schedule:
        scheduler.add_job(
            _auto_build_daily_schedule, "date",
            run_date=_utc_now_naive() + timedelta(seconds=5),
            id="auto-daily-schedule-startup", replace_existing=True,
        )
        hour, minute = _auto_build_utc_cron()
        scheduler.add_job(
            _auto_build_daily_schedule, "cron",
            hour=hour, minute=minute,
            id="auto-daily-schedule", replace_existing=True,
        )
        logger.info("Scheduled daily auto timetable build (UTC %02d:%02d)", hour, minute)

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
    # Shutdown must survive a failing component: if, say, the Telegram
    # updater errors out of its stop sequence, the scheduler would never be
    # shut down and the final HF backup would silently skip - losing the
    # last data writes on ephemeral hosts.
    try:
        # Unconditional: stop() itself guards on whether the Application was
        # ever built, and must run even when polling never started.
        await telegram.stop()
    except Exception as exc:  # noqa: BLE001 - keep tearing down regardless
        logger.warning("Telegram shutdown raised: %s", exc)
    
    # Properly remove all scheduled jobs before shutdown to prevent leaks
    try:
        job_ids = [
            "reminder-sweep",
            "schedule-block-sweep",
            "hf-backup",
            "auto-daily-schedule-startup",
            "auto-daily-schedule",
        ]
        # Also remove any block notification jobs
        for job in scheduler.get_jobs():
            if job.id.startswith("block-"):
                job_ids.append(job.id)
        
        for job_id in job_ids:
            job = scheduler.get_job(job_id)
            if job:
                job.remove()
                logger.debug("Removed scheduler job: %s", job_id)
        
        scheduler.shutdown(wait=False)
    except Exception as exc:  # noqa: BLE001 - keep tearing down regardless
        logger.warning("Scheduler shutdown raised: %s", exc)
    # Final snapshot; force=True waits out any in-flight periodic upload
    # instead of silently skipping (which would lose the last writes).
    try:
        await hf_backup.upload(force=True)
    except Exception as exc:  # noqa: BLE001 - already exiting; don't mask the rest
        logger.warning("Final HF backup raised: %s", exc)


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)

# ---- CORS Configuration ----
# Allow credentials only for specific origins, not wildcard
allowed_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
# Add any additional origins from env
if os.environ.get("EMMA_ALLOWED_ORIGINS"):
    allowed_origins.extend([o.strip() for o in os.environ["EMMA_ALLOWED_ORIGINS"].split(",")])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Cookie"],
    expose_headers=["Set-Cookie"],
)

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
    _login_page = str(WEB_DIR / "login.html")

    @app.get("/login")
    @app.get("/login/")
    def login_page():
        return FileResponse(_login_page)


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

# Login attempt limiting: 3 failed attempts per client locks login for 15 min.
_ATTEMPT_LIMIT = 3
_ATTEMPT_LOCK_SECONDS = 15 * 60
_login_attempts: dict[str, dict] = {}

def _login_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"

def _check_login_lock(key: str) -> None:
    rec = _login_attempts.get(key)
    if rec and rec.get("locked_until"):
        if time.time() < rec["locked_until"]:
            wait = int(rec["locked_until"] - time.time()) + 1
            raise HTTPException(
                429,
                f"Too many failed attempts — locked. Try again in {wait}s",
                headers={"Retry-After": str(wait)},
            )
        _login_attempts.pop(key, None)

@app.post("/api/auth")
def login(payload: LoginRequest, request: Request, response: Response):
    logger.info("login attempt — password set=%s, hash_len=%s", bool(settings.web_password), len(_get_web_password_hash()) if _get_web_password_hash() else 0)
    key = _login_key(request)
    _check_login_lock(key)
    
    password_hash = _get_web_password_hash()
    if not password_hash:
        logger.error("web_password not configured")
        raise HTTPException(500, "Server not configured for web auth")
    
    if _verify_password(payload.password, password_hash):
        _login_attempts.pop(key, None)
        # Create JWT session token
        token = _create_session_token({"sub": "user", "type": "web"})
        response = JSONResponse({"ok": True})
        # Render terminates TLS at load balancer; check X-Forwarded-Proto
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        is_https = forwarded_proto == "https" or request.url.scheme == "https"
        response.set_cookie(
            "emma_session",
            token,
            httponly=True,
            secure=is_https,
            samesite="lax" if is_https else "none",
            path="/",
            max_age=JWT_EXPIRY_HOURS * 3600,
        )
        return response
    rec = _login_attempts.setdefault(key, {"fails": 0, "locked_until": 0})
    rec["fails"] += 1
    if rec["fails"] >= _ATTEMPT_LIMIT:
        rec["locked_until"] = time.time() + _ATTEMPT_LOCK_SECONDS
        logger.warning("login locked out (ip=%s, fails=%s)", key, rec["fails"])
        raise HTTPException(
            429,
            detail="Too many failed attempts — locked. Try again in 15 minutes",
            headers={"Retry-After": str(_ATTEMPT_LOCK_SECONDS)},
        )
    left = _ATTEMPT_LIMIT - rec["fails"]
    logger.warning("login failed — wrong password (ip=%s, fails=%s)", key, rec["fails"])
    raise HTTPException(401, f"Wrong password. {left} attempt(s) left")


@app.post("/api/auth/logout")
def logout(response: Response):
    response = JSONResponse({"ok": True})
    response.delete_cookie("emma_session", path="/")
    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not settings.web_password:
        return await call_next(request)
    # Public paths: the login page itself, its endpoint, health + root status
    if request.url.path in ("/", "/health", "/api/auth", "/api/auth/logout", "/login", "/login/"):
        return await call_next(request)
    # Check Authorization header for Bearer token (JWT)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):]
        payload = _decode_session_token(token)
        if payload:
            request.state.user = payload
            return await call_next(request)
    # Check cookie (set after successful login)
    token = request.cookies.get("emma_session")
    if token:
        payload = _decode_session_token(token)
        if payload:
            request.state.user = payload
            return await call_next(request)
    # Normal pages are only reachable when authorized; otherwise bounce to login
    if request.url.path.startswith("/ui/"):
        return RedirectResponse("/login/", status_code=302)
    return JSONResponse({"detail": "Unauthorized"}, status_code=401)
