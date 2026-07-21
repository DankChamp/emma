"""
Emma Voice - a wake-word + speech front end.

Same architectural rule as gui/: this is just another client of Emma's
HTTP API. It never imports from core/ directly. That means the CLI, the
desktop GUI, and this voice loop are all equally "just clients" - no
business logic lives here, only audio in/out and an HTTP call.

Everything in this package runs fully on the local machine:
  - wake-word + command transcription: Vosk (offline speech recognition)
  - reply playback: pyttsx3 (offline text-to-speech)

No audio ever leaves the machine except as text sent to whatever AI
provider Emma's router picks for the reply (which can itself be a fully
local model - see core/router/providers/local_generic.py and local_ollama.py).

Includes a local command router (`VoiceCommandRouter`) that intercepts offline commands
(like adding/listing tasks, setting reminders, managing status/busy mode, saving memories)
directly without hitting the LLM backend.
"""
