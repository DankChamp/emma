# Emma — Base Architecture

This is Emma: not a chatbot, an operating system for daily life, with the
LLM as just one of its tools.

Everything here is real, tested, working code. Every endpoint was hit with
a test client, and the full stack (server + CLI) was smoke-tested by
actually starting the server and running each CLI command against it.

## Quick start

```bash
cd emma
./setup.sh      # one-time: venv, deps, .env
./run.sh        # start the server (http://localhost:8000, docs at /docs)
```

In another terminal:

```bash
./emma task add "Fix WebRTC combat sync" --project motion-capture --priority high
./emma task list
./emma morning
./emma busy "deep work session"
./emma status
./emma free
./emma night
```

`./emma` is a thin wrapper so you don't have to type
`.venv/bin/python emma_cli.py` every time. `make run`, `make dev` (autoreload),
and `make cli ARGS="task list"` work too if you prefer Make.

## Desktop GUI

A PySide6 desktop app ("Emma Desktop") lives in `gui/`. It's a plain HTTP
client of the API above - same relationship a future phone app or ESP32
voice device would have - so it never touches `core/` directly.

```bash
./run.sh      # terminal 1: backend must be running first
./run_gui.sh  # terminal 2: opens the window
```

What it gives you:

- **Talk** — normal chat. Auto mode lets Emma's router pick the model per
  message (via the Task dropdown); Manual mode pins one provider/model for
  everything you send. A **Save to Memory** button on the chat tab lets you
  push any exchange into long-term memory, project memory, or both.
- **Voice** — start/stop the wake-word assistant (see below) and watch its
  log, without needing a second terminal.
- **Providers & Keys** — paste/replace each provider's API key, see live
  online/offline status, pick or refresh its default model, and test the
  connection. Everything here is written straight into `.env`, so it
  survives a restart with no manual file editing.
- **Memory** — browse and hand-edit long-term, project, and daily memory.
- **Tasks**, **Reminders**, **Busy Mode** — the same features as the `./emma`
  CLI, in a window.

`run_gui.sh` points at `http://127.0.0.1:8000` by default; pass a different
URL as an argument (or set `EMMA_GUI_BASE_URL`) if the backend runs elsewhere.

## Voice: wake word + speech

`voice/` is a wake-word front end - say **"hey emma"**, then your command,
and Emma answers out loud in a natural, feminine voice. Same architecture as
everything else: it's just another HTTP client of the API, doing audio
in/out and nothing else. Everything runs on your machine; no audio is ever
sent anywhere except as text, to whichever AI provider the router picks.

Emma's voice is **Piper**, a small neural TTS engine that runs on the CPU
and sounds genuinely human - a big step up from the old robotic `espeak`
fallback. The voice model is a local file; once downloaded, speech is fully
offline.

```bash
./run.sh        # terminal 1: backend must already be running
./run_voice.sh  # terminal 2: starts listening for "hey emma"
```

One-time setup:

1. `pip install -r requirements.txt` (pulls in `vosk`, `sounddevice`,
   `piper-tts`, `pyttsx3`)
2. Linux only: `sudo apt install libportaudio2` (and, only if you want the
   robotic fallback voice, `espeak-ng`)
3. Download a [Vosk](https://alphacephei.com/vosk/models) speech model for
   recognition - `vosk-model-small-en-us-0.15` (~40MB) is a good starting
   point. Unzip it and set `VOICE_VOSK_MODEL_PATH` in `.env` to that folder.
4. Give Emma her voice: `python voice/download_voice.py` grabs a natural
   feminine voice (**Amy**, ~63MB) into `voice/models/`, auto-detected at
   runtime. Try `--list` for other curated voices (British "Jenny", the
   crisp "hfc_female", the tiny fast "Kathleen", ...).

Useful flags/env vars:

- `VOICE_WAKE_WORD` / `--wake-word "hey jarvis"` - change the wake phrase.
  Matching is fuzzy (`voice/matcher.py`) so it tolerates the odd
  mis-transcription instead of demanding an exact match.
- `python emma_voice.py --list-devices` - find your microphone's name/index
  for `VOICE_INPUT_DEVICE` / `--device`.
- `python emma_voice.py --list-voices` - show installed neural voices (and
  system fallback voices).
- `VOICE_TTS_ENGINE` / `--engine` - `auto` (Piper if installed, else system),
  `piper`, or `pyttsx3`.
- `VOICE_PIPER_MODEL_PATH` / `--piper-model` - pick a specific voice by name
  or path when you have several installed.
- `VOICE_PIPER_LENGTH_SCALE` / `--length-scale` - pacing (`1.0` natural, `>1`
  slower, `<1` faster). `VOICE_PIPER_NOISE_SCALE` / `VOICE_PIPER_NOISE_W_SCALE`
  tune expressiveness and cadence; `VOICE_PIPER_VOLUME` sets output gain.

Or just click **Start Listening** on the GUI's Voice tab, which runs the
same script as a background process.

## Local models: bring your own

Emma already talks to **Ollama** (`core/router/providers/local_ollama.py`).
On top of that, `core/router/providers/local_generic.py` speaks the
OpenAI-compatible chat-completions API that most local inference tools
expose, so you can point Emma at **any** local model server:

- LM Studio (Local Server tab, default `http://localhost:1234`)
- llama.cpp's `llama-server` (default `http://localhost:8080`)
- text-generation-webui (`--api` flag / openai extension)
- vLLM's OpenAI-compatible server
- KoboldCpp, LocalAI, and similar

Set it up from the GUI's **Providers & Keys → Local Server (Any Model)**
card (base URL, optional API key if your server wants one, and default
model - "Refresh Models" pulls the live list from `/v1/models`), or by hand
in `.env`:

```
LOCAL_BASE_URL=http://localhost:1234
LOCAL_API_KEY=
LOCAL_DEFAULT_MODEL=your-model-name
```

`PREFER_LOCAL_WHEN_AVAILABLE=true` (the default) puts both Ollama and this
generic local provider ahead of any cloud provider in the routing table
whenever they're reachable.

### Running Emma as a background service (Void Linux / runit)

Void uses runit, not systemd, so a runit service is included:

```bash
# edit contrib/runit/emma/run first - set EMMA_DIR to your actual path
sudo ln -s /path/to/emma/contrib/runit/emma /var/service/emma
sv status emma
```

## Structure

```
emma/
├── main.py                    # FastAPI app assembly + scheduler lifespan
├── config.py                  # All settings, env-driven, no hardcoded keys
├── emma_cli.py / ./emma        # Terminal client - thin HTTP wrapper, no logic of its own
├── setup.sh / run.sh / Makefile
├── contrib/runit/emma/         # Void Linux runit service files
├── core/                      # All business logic - framework-agnostic
│   ├── router/                 # AI Router: decides which model handles what
│   ├── memory/                  # Four-tier memory: long-term / project / daily / conversation
│   ├── tasks/                   # Task manager - create/edit/delete/prioritize/complete
│   ├── reminders/                # APScheduler-backed, repeat + duration-based creation
│   ├── busy_mode/                 # Interruption gating + contact auto-notify
│   └── planning/                   # Morning briefing / night review
└── api/                        # FastAPI layer - thin, no logic of its own
    ├── deps.py                  # Dependency injection wiring
    └── routes/                  # tasks, reminders, chat, memory, planning, status
```

## What's new since the base skeleton

- **Daily Planning** (`core/planning/`) — `GET /planning/morning` returns
  pending/overdue/due-today tasks, a workload estimate, and up to 3
  suggested priorities (plus an AI-generated narrative if a provider is
  available, silently omitted otherwise). `GET /planning/night` reports
  what got completed today and what's carrying over tomorrow.

- **Busy Mode** (`core/busy_mode/`) — `POST /status/busy` /
  `POST /status/free` toggle a single persisted state. While busy, only
  reminders flagged `important=true` fire — everything else is gated by
  `BusyModeManager.should_interrupt()`, which `ReminderManager` consults
  automatically via an injected callback. Contacts registered with
  `POST /status/contacts` get auto-notified on busy/free transitions
  through a `MessengerAdapter` interface (currently a console-logging stub
  — same extension pattern as `AIProvider`, ready for a real WhatsApp/Telegram
  adapter later).

- **CLI** (`emma_cli.py`) — a pure HTTP client over the API. This matters
  architecturally: the CLI, a future PySide6 desktop app, and any voice
  frontend are all equally "just clients" — no business logic lives in
  any of them.

- **One-command execution** — `setup.sh` (venv + deps + `.env`), `run.sh`
  (start, or `--dev` for autoreload), `Makefile`, and a runit service for
  Void Linux instead of assuming systemd.

## Why it's built this way

- **Router pattern for AI**: `core/router/router.py` holds a
  `TaskType -> [providers in preference order]` table. Adding a future
  provider means writing one class in `providers/` that implements
  `AIProvider` — nothing else changes.

- **Memory is four separate SQLite tables, not a text file**: long-term,
  project-scoped, daily, and conversation, each with its own manager
  methods so nothing accidentally mixes tiers.

- **Dependency injection, not globals**: every route asks for
  `Depends(get_task_manager)` etc. Each connection is request-scoped
  (`check_same_thread=False` is safe here specifically because connections
  are never shared across requests — each dependency call opens a fresh one).

- **SQLite now, Postgres-ready later**: all queries are plain SQL, no ORM
  lock-in.

## Bugs caught during testing (fixed, documented so you know why the code looks this way)

- A bare `Settings` type-hinted parameter with a `None` default in a
  dependency function made FastAPI think it was a request body field.
  Fixed by using `Depends(get_settings)` everywhere instead of a bare default.
- `AsyncIOScheduler.start()` was being called from a sync dependency
  function running in FastAPI's threadpool, which has no running event
  loop. Fixed by starting/stopping the scheduler once in the app's
  `lifespan` handler instead.
- A method named `list()` on `TaskManager` shadowed the builtin `list` for
  every annotation written *after* it in the same class body. Fixed by
  using `typing.List` for the later method.
- SQLite connections were created inside FastAPI's sync threadpool but
  used later from the async event loop thread, tripping Python's
  same-thread check. Fixed with `check_same_thread=False`, which is safe
  here because every connection is created fresh per-request and never
  shared.

## What's deliberately NOT here yet

- Contact automation's real messaging backend (WhatsApp/Telegram/etc — the
  `MessengerAdapter` interface is ready, no adapter implemented)
- ESP32 hardware voice device (`voice/` is the desktop version of this -
  same wake-word + speech idea, just running on the ThinkPad's own mic and
  speakers instead of a standalone board)
- ChromaDB / semantic recall — an additional memory tier for fuzzy retrieval

