"""
ChatterboxBackend - Emma's neural voice engine (sidecar subprocess bridge).

Chatterbox-Turbo (350M, with the smaller 110M Nano as a CPU fallback) is
the voice Emma speaks with: a voice-cloned, natural-sounding neural TTS.
It runs in `.venv-chatterbox`, a sidecar virtualenv owned by uv with
Python 3.11, because the main .venv runs Python 3.14 and the pinned torch
builds don't exist for it.

Talking to the model across processes keeps host/guest Python apart
cleanly. The protocol (see chatterbox_worker.py) is a handful of
JSON-lines requests; synthesis latency per sentence is seconds, so the
round-trip overhead is noise.

Barge-in works the same way as Piper: playback of each synthesized
sentence is interruptible via `_play_pcm`'s stop event. Synthesis of the
current sentence can't be interrupted mid-forward (it's a blocking CPU
call in the worker), which is the same granularity Piper's per-chunk
interrupts give us.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import select
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("emma.voice.tts.chatterbox")

EMMA_ROOT = Path(__file__).resolve().parent.parent
SIDECAR_PYTHON = EMMA_ROOT / ".venv-chatterbox" / "bin" / "python"
WORKER_SCRIPT = Path(__file__).resolve().parent / "chatterbox_worker.py"
# Where the worker keeps model weights; also holds the cached benchmark.
MODELS_DIR = Path(__file__).resolve().parent / "models" / "chatterbox"
BENCH_FILE = MODELS_DIR / ".benchmark.json"

# Longer than the slowest realistic CPU sentence; guards against a wedged worker.
REQUEST_TIMEOUT_S = 300.0
# Upper bound on a single reply line (the PCM payload makes these big).
_MAX_REPLY_BYTES = 32 * 1024 * 1024
# Above this realtime-factor the engine falls back to Nano on slow CPUs.
SLOW_CPU_RTF = 1.2


class ChatterboxUnavailableError(RuntimeError):
    """Chatterbox can't run here, with a message saying how to fix it."""


class ChatterboxBackend:
    """Voice-cloned neural TTS via the sidecar worker. One model, cached."""

    def __init__(
        self,
        reference_wav: str,
        variant: str = "turbo",
        auto_fallback: bool = True,
    ):
        self.reference_wav = Path(reference_wav).expanduser()
        self.variant = variant
        self.auto_fallback = auto_fallback
        self.sample_rate = 24000
        self.backend_name = f"chatterbox-{variant}"
        self._proc: Optional[subprocess.Popen] = None
        self._next_id = 0
        self._reloads = 0
        # Bytes read from the worker but not yet consumed as a full line.
        self._inbuf = b""

        if not SIDECAR_PYTHON.is_file():
            raise ChatterboxUnavailableError(
                "The chatterbox sidecar isn't installed. Run: "
                "uv venv .venv-chatterbox --python 3.11 && "
                "uv pip install --python .venv-chatterbox chatterbox-tts"
            )
        if not self.reference_wav.is_file():
            raise ChatterboxUnavailableError(
                f"Reference voice not found at {self.reference_wav}. Set "
                "VOICE_CHATTERBOX_REFERENCE_WAV to a ~10s WAV of the voice "
                "Emma should be cloned from (see voice/check_reference.py)."
            )

    def start(self) -> None:
        """
        Spawn the worker, load the model, and - when Turbo proves slower
        than realtime on this CPU - automatically fall back to Nano. The
        benchmark is cached after the first run, so Turbo is never loaded
        pointlessly on a machine that already proved itself too slow.
        Model load takes tens of seconds (first run also downloads
        weights), so this is logged, not hidden.
        """
        if (
            self.auto_fallback
            and self.variant == "turbo"
            and self._cached_turbo_rtf() > SLOW_CPU_RTF
        ):
            logger.info(
                "This machine previously benchmarked Turbo at %.2fx realtime - "
                "too slow for natural conversation, using Nano directly "
                "(override with VOICE_CHATTERBOX_VARIANT=turbo or "
                "VOICE_CHATTERBOX_AUTO_FALLBACK=false).",
                self._cached_turbo_rtf(),
            )
            self.variant = "nano"
            self.backend_name = "chatterbox-nano"

        reply = self._load_with(self.variant)
        if not reply.get("ok"):
            raise RuntimeError(f"Chatterbox failed to load: {reply.get('error', '?')}")

        self.sample_rate = reply["sr"]
        rtf = reply.get("rtf", 1.0)
        logger.info(
            "Chatterbox-%s ready (load %.1fs, realtime factor %.2f%s)",
            self.variant.upper(),
            reply.get("load_s", 0),
            rtf,
            " - slower than realtime" if rtf > 1.0 else "",
        )

        if self.auto_fallback and self.variant == "turbo" and rtf > SLOW_CPU_RTF:
            self._cache_benchmark(rtf, chosen="nano")
            logger.info(
                "Turbo is %.2fx slower than realtime on this CPU; falling back "
                "to Nano, which shares its architecture at 3x realtime. Set "
                "VOICE_CHATTERBOX_VARIANT=nano (or disable the fallback) to override.",
                rtf,
            )
            self._quit()
            reply = self._load_with("nano")
            if not reply.get("ok"):
                raise RuntimeError(f"Chatterbox Nano failed to load: {reply.get('error', '?')}")
            self.variant = "nano"
            self.backend_name = "chatterbox-nano"
            self.sample_rate = reply["sr"]
            logger.info(
                "Chatterbox-NANO ready (load %.1fs, realtime factor %.2f)",
                reply.get("load_s", 0),
                reply.get("rtf", 1.0),
            )
        else:
            if self.variant == "turbo":
                self._cache_benchmark(rtf, chosen=self.variant)

    @staticmethod
    def _cached_turbo_rtf() -> float:
        try:
            data = json.loads(BENCH_FILE.read_text())
            return float(data.get("turbo_rtf", 0.0))
        except Exception:  # noqa: BLE001 - no benchmark yet
            return 0.0

    @staticmethod
    def _cache_benchmark(rtf: float, chosen: str) -> None:
        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            BENCH_FILE.write_text(
                json.dumps({"turbo_rtf": rtf, "chosen_variant": chosen})
            )
        except OSError:  # noqa: BLE001 - caching is an optimization, not a requirement
            logger.debug("Couldn't cache the TTS benchmark")

    def _load_with(self, variant: str) -> dict:
        self._spawn()
        reply = self._request(
            {"cmd": "load", "variant": variant, "reference_wav": str(self.reference_wav)}
        )
        if not reply.get("ok"):
            # A failed load leaves a half-dead worker; start clean next time.
            self._quit()
        return reply

    def _spawn(self) -> None:
        # Binary pipes on purpose: a TextIOWrapper buffers the worker's
        # whole line into its own internal buffer, so select() on the raw fd
        # never fires again even though unread bytes are available. With
        # bytes + read1() we always get exactly what the pipe has right now.
        self._proc = subprocess.Popen(
            [str(SIDECAR_PYTHON), str(WORKER_SCRIPT)],
            cwd=str(EMMA_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=os.environ.copy(),
        )

    def _request(self, payload: dict) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            raise ChatterboxUnavailableError("The chatterbox worker died.")
        assert self._proc.stdin is not None and self._proc.stdout is not None

        try:
            self._proc.stdin.write(
                json.dumps(payload, ensure_ascii=True).encode("utf-8") + b"\n"
            )
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._quit()
            raise ChatterboxUnavailableError("The chatterbox worker died mid-request.")

        # Read a complete line with an overall deadline. select() alone only
        # bounds the wait for the first byte, and unbounded readline() would
        # hang forever on a wedged worker that wrote a partial line. The
        # worker's replies are JSON-lines of ASCII (json.dumps escapes
        # non-ASCII by default), so lines split mid-multibyte-char decode
        # safely once they are complete.
        deadline = time.monotonic() + REQUEST_TIMEOUT_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._quit()
                raise RuntimeError("The chatterbox worker didn't answer in time.")
            if len(self._inbuf) > _MAX_REPLY_BYTES:
                self._quit()
                raise ChatterboxUnavailableError("The chatterbox worker replied with garbage.")
            ready, _, _ = select.select([self._proc.stdout], [], [], min(remaining, 5.0))
            if not ready:
                continue
            try:
                chunk = self._proc.stdout.read1(65536)
            except (OSError, ValueError):
                self._quit()
                raise ChatterboxUnavailableError("The chatterbox worker died mid-request.")
            if not chunk:
                self._quit()
                raise ChatterboxUnavailableError("The chatterbox worker died mid-request.")
            self._inbuf += chunk
            if b"\n" not in self._inbuf:
                continue
            line, _, self._inbuf = self._inbuf.partition(b"\n")
            try:
                reply = json.loads(line.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._quit()
                raise ChatterboxUnavailableError("The chatterbox worker sent garbage back.")
            if reply.get("id") != payload.get("id"):
                # A stale reply from a timed-out earlier request would otherwise
                # be consumed as if it were this request's answer, leaving the
                # pipe one reply behind forever. Drop the whole worker instead.
                self._quit()
                raise ChatterboxUnavailableError("The chatterbox worker replied out of order.")
            return reply

    def _quit(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                assert self._proc.stdin is not None
                self._proc.stdin.write(json.dumps({"cmd": "quit"}).encode("utf-8") + b"\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=10.0)
        except Exception:  # noqa: BLE001 - worker is going down anyway
            try:
                self._proc.kill()
                self._proc.wait(timeout=5.0)
            except Exception:  # noqa: BLE001 - reap best effort
                pass
        self._proc = None

    def _reload(self) -> None:
        """Respawn the worker and reload the model after a crash or a
        stale-reply desync, so the voice loop can heal itself."""
        reply = self._load_with(self.variant)
        if not reply.get("ok"):
            raise ChatterboxUnavailableError(
                f"Chatterbox reload failed: {reply.get('error', '?')}"
            )
        self.sample_rate = reply["sr"]

    def say(self, text: str, stop_event: Optional[threading.Event] = None) -> bool:
        """
        Synthesize `text` and play it. Returns True if it played to the
        end, False if `stop_event` cut playback short (barge-in).
        """
        if not text or not text.strip():
            return True
        if stop_event is not None and stop_event.is_set():
            return False

        try:
            reply = self._request({"id": self._next_id, "text": text})
        except ChatterboxUnavailableError:
            # Worker died or the pipe desynced - respawn and reload once so
            # the voice loop heals itself instead of dying with a traceback.
            if self._reloads >= 2:
                raise
            self._reloads += 1
            logger.warning("Chatterbox worker lost; reloading the model once.")
            self._reload()
            return self.say(text, stop_event=stop_event)
        self._next_id += 1
        if not reply.get("ok"):
            # Synthesis itself failed - the worker is still alive, so retry
            # once against a fresh model rather than giving up.
            if self._reloads < 2:
                self._reloads += 1
                logger.warning("Chatterbox synthesis failed; reloading the model once.")
                self._reload()
                return self.say(text, stop_event=stop_event)
            raise RuntimeError(f"Chatterbox synthesis failed: {reply.get('error', '?')}")

        self._reloads = 0
        pcm = base64.b64decode(reply["pcm_b64"])
        from .tts import _play_pcm

        return _play_pcm(pcm, self.sample_rate, stop_event=stop_event)