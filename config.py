"""
Emma - Central Configuration

Single source of truth for all settings. Everything is env-driven so
Emma never has secrets or provider choices baked into code.

Copy .env.example to .env and fill in what you have. Anything you leave
blank simply disables that provider - the router will skip it.
"""
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_name: str = "Emma"
    debug: bool = True

    # --- Databases (SQLite now, swappable for Postgres later) ---
    memory_db_path: Path = DATA_DIR / "memory.db"
    busy_mode_db_path: Path = DATA_DIR / "busy_mode.db"
    schedule_db_path: Path = DATA_DIR / "schedule.db"
    tasks_db_path: Path = DATA_DIR / "tasks.db"
    reminders_db_path: Path = DATA_DIR / "reminders.db"
    profile_db_path: Path = DATA_DIR / "profile.db"

    # --- Provider credentials (leave blank to disable a provider) ---
    groq_api_key: Optional[str] = None
    nvidia_nim_api_key: Optional[str] = None
    ollama_base_url: str = "http://localhost:11434"

    # --- Generic local provider: any OpenAI-compatible server (LM Studio,
    # llama.cpp's llama-server, text-generation-webui, vLLM, KoboldCpp, ...).
    # Leave local_base_url blank to disable it entirely.
    local_base_url: Optional[str] = None
    local_api_key: Optional[str] = None
    local_default_model: Optional[str] = None

    # --- Default models per provider ---
    ollama_default_model: str = "llama3.1:8b"
    groq_default_model: str = "llama-3.3-70b-versatile"
    nvidia_nim_default_model: str = "meta/llama-3.1-8b-instruct"

    # --- Routing behavior ---
    # If True, Emma prefers local (Ollama / the generic local provider)
    # whenever reachable, falling back to cloud providers only if local is
    # down or unsuitable for the task.
    prefer_local_when_available: bool = True

    # --- Voice (wake word + speech, see voice/) ---
    # Everything here is offline/local - no audio ever leaves the machine.
    voice_wake_word: str = "hey emma"
    voice_backend_url: str = "http://127.0.0.1:8000"
    voice_vosk_model_path: Optional[str] = None  # path to an unzipped Vosk model dir
    voice_input_device: Optional[str] = None  # sounddevice device name/index, None = system default
    voice_command_timeout_seconds: float = 8.0
    voice_silence_seconds: float = 1.2
    # Barge-in: let the wake word interrupt Emma while she's speaking. Say
    # "hey emma" over her reply to cut her off and immediately give a new
    # command. Turn off if she keeps hearing her own voice through the mic.
    voice_barge_in: bool = True

    # --- Text-to-speech (how Emma sounds) ---
    # Engine: "auto" uses the natural neural Piper voice if a model is
    # installed (run `python voice/download_voice.py`) and quietly falls back
    # to the robotic system voice otherwise; "piper" forces neural; "pyttsx3"
    # forces the legacy system voice.
    voice_tts_engine: str = "auto"
    # Piper voice model. Blank = auto-pick a feminine voice from voice/models.
    # Can be a full path or a bare name like "en_US-amy-medium".
    voice_piper_model_path: Optional[str] = None
    # Piper voice tuning (all optional, sensible feminine defaults):
    #   length_scale  - pacing; 1.0 = natural, >1 slower, <1 faster
    #   noise_scale   - expressiveness/variation in intonation
    #   noise_w_scale - variation in phoneme duration (cadence)
    #   volume        - 0.0..1.0 output gain
    #   speaker_id    - for multi-speaker models only
    voice_piper_length_scale: float = 1.02
    voice_piper_noise_scale: float = 0.667
    voice_piper_noise_w_scale: float = 0.8
    voice_piper_volume: float = 1.0
    voice_piper_speaker_id: Optional[int] = None

    # Legacy pyttsx3 knobs (used only when the engine falls back to pyttsx3):
    voice_tts_rate: int = 175
    voice_tts_voice: Optional[str] = None  # substring match against pyttsx3 voice names/ids

    # --- Time zone (IANA name like Asia/Kolkata, America/New_York) ---
    # Used to correctly schedule notifications from local timetable times.
    tz: str = "Asia/Kolkata"

    # --- Telegram notification bot ---
    telegram_bot_token: Optional[str] = None
    owner_name: str = "VOID"
    owner_telegram_id: Optional[int] = None
    notifications_db_path: Path = DATA_DIR / "notifications.db"
    appointments_db_path: Path = DATA_DIR / "appointments.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()


ENV_PATH = BASE_DIR / ".env"


def update_env_file(updates: dict[str, str]) -> None:
    """
    Persist one or more KEY=VALUE pairs into .env, preserving every other
    line untouched (comments, ordering, unrelated keys).

    This is what lets the GUI's "Save" button on an API key or model field
    actually stick across restarts, instead of only living in memory.
    Always followed by clearing the settings cache so the next request
    picks up the new value immediately - no restart needed.
    """
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []

    remaining = dict(updates)
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        existing_key = stripped.split("=", 1)[0].strip()
        if existing_key in remaining:
            new_lines.append(f"{existing_key}={remaining.pop(existing_key)}")
        else:
            new_lines.append(line)

    for key, value in remaining.items():
        new_lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n")
    get_settings.cache_clear()
