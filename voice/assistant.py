"""
VoiceAssistant - ties the wake-word listener, Emma's backend, and TTS
together into the loop `emma_voice.py` runs:

    IDLE (listening for "hey emma")
      -> wake word heard
    LISTENING (recording the command)
      -> silence detected / command captured
    THINKING (intent gate + streaming replies from Emma's backend)
      -> her reply trickles in from the SSE stream
    SPEAKING (each finished sentence is synthesized and read aloud)
      -> back to IDLE

The reply is streamed sentence-by-sentence: as soon as the LLM finishes a
sentence, that sentence is spoken while the rest is still being generated
- Emma never waits for her whole reply to exist before starting to talk.
Before anything is spoken, the backend's intent gate decides whether the
wake-word utterance is actually addressed to her.
"""
from __future__ import annotations

import logging
import random
import threading
from typing import Callable, Optional

import httpx

from .client import VoiceBackendClient
from .speech_formatter import SentenceAccumulator
from .tts import Speaker
from .wake_word import WakeWordListener, load_model

logger = logging.getLogger("emma.voice.assistant")

# Short, warm acknowledgement lines so there's audible feedback the wake
# word was heard and her brain is actually answering before the real reply
# starts flowing. Kept friendly and natural to match Emma's feminine voice.
_ACK_PHRASES = ["Yes?", "Mm-hmm?", "I'm here.", "Go ahead.", "I'm listening."]

# Subtle thinking sound phrases - played when Emma delegates to Luna/Aqua
_THINKING_SOUNDS = [
    "Hmm...",
    "Let me think...",
    "One moment...",
    "Checking with Luna...",
]

# Spoken when the mic caught nothing intelligible.
_MISHEARD_PHRASES = [
    "Sorry, I didn't quite catch that.",
    "Hmm, I missed that - could you say it again?",
    "Sorry, could you repeat that for me?",
]

# Spoken when something went wrong talking to the backend.
_ERROR_PHRASE = "Sorry, I ran into a problem handling that. Let's try again in a moment."
# Spoken when the backend isn't running at all - the most common failure.
_UNREACHABLE_PHRASE = "Sorry, I can't reach my server right now."

# Phrases that indicate Emma is delegating to a subordinate
_DELEGATION_INDICATORS = [
    "i'll ask luna",
    "i'll have luna",
    "let me ask luna",
    "delegating to luna",
    "luna's on it",
    "i'll pass that to aqua",
    "let me get aqua",
]


class VoiceAssistant:
    def __init__(
        self,
        backend_url: str,
        wake_word: str = "hey emma",
        local_only: bool = True,
        vosk_model_path: Optional[str] = None,
        input_device: Optional[str] = None,
        tts_rate: int = 175,
        tts_voice: Optional[str] = None,
        tts_engine: str = "auto",
        piper_model_path: Optional[str] = None,
        piper_length_scale: Optional[float] = None,
        piper_noise_scale: float = 0.667,
        piper_noise_w_scale: float = 0.8,
        piper_volume: float = 1.0,
        piper_speaker_id: Optional[int] = None,
        chatterbox_reference_wav: Optional[str] = None,
        chatterbox_variant: str = "turbo",
        chatterbox_auto_fallback: bool = True,
        command_timeout: float = 8.0,
        silence_seconds: float = 1.2,
        judge_enabled: bool = True,
        stream_min_chars: int = 15,
        stream_max_chars: int = 320,
        on_state_change: Optional[Callable[[str], None]] = None,
        speak_acknowledgement: bool = True,
        barge_in: bool = True,
    ):
        self.client = VoiceBackendClient(backend_url, local_only=local_only)
        self.speaker = Speaker(
            rate=tts_rate,
            voice_hint=tts_voice,
            engine=tts_engine,
            piper_model_path=piper_model_path,
            length_scale=piper_length_scale,
            noise_scale=piper_noise_scale,
            noise_w_scale=piper_noise_w_scale,
            volume=piper_volume,
            speaker_id=piper_speaker_id,
            chatterbox_reference_wav=chatterbox_reference_wav,
            chatterbox_variant=chatterbox_variant,
            chatterbox_auto_fallback=chatterbox_auto_fallback,
        )
        logger.info("Emma's voice backend: %s", self.speaker.backend_name)
        self.wake_word = wake_word
        self.command_timeout = command_timeout
        self.silence_seconds = silence_seconds
        self.on_state_change = on_state_change or (lambda state: None)
        self.speak_acknowledgement = speak_acknowledgement
        self.judge_enabled = judge_enabled
        self.stream_min_chars = stream_min_chars
        self.stream_max_chars = stream_max_chars
        # Barge-in only makes sense with a neural voice, which we can
        # interrupt promptly. It also needs a mic that isn't the same audio
        # path as the speaker; on a laptop that's fine. Can be turned off
        # (--no-barge-in) if Emma keeps hearing her own voice.
        self.barge_in = barge_in

        from .commands import VoiceCommandRouter
        self.router = VoiceCommandRouter(self.client)
        self._persona = self.client.get_persona()

        model = load_model(vosk_model_path)
        self.listener = WakeWordListener(model, wake_word=wake_word, device=input_device)

    def run_forever(self, stop_check: Optional[Callable[[], bool]] = None) -> None:
        """
        The main loop. `stop_check`, if given, is polled so a caller (the
        GUI's Stop button, or Ctrl+C via a signal handler) can end things
        between cycles instead of only at wake-word-listen granularity.
        """
        if not self.client.is_reachable():
            logger.warning(
                "Can't reach Emma's backend at %s yet - make sure `./run.sh` is running. "
                "Will keep listening for the wake word anyway and retry the backend when needed.",
                self.client.base_url,
            )

        while stop_check is None or not stop_check():
            self._cycle(stop_check)

    def _cycle(self, stop_check: Optional[Callable[[], bool]]) -> None:
        self.on_state_change("idle")
        heard = self.listener.wait_for_wake_word(stop_check=stop_check)
        if not heard[0] or (stop_check is not None and stop_check()):
            return
        wake_tail = heard[1]

        # Once woken, stay in a tight ask -> answer -> (maybe interrupted) ->
        # ask again loop. A barge-in during Emma's reply drops us straight
        # back into capturing a new command, so the user doesn't have to say
        # the wake word again just to correct or redirect her mid-sentence.
        acknowledge = self.speak_acknowledgement
        while stop_check is None or not stop_check():
            self.on_state_change("listening")
            command = self.listener.capture_command(
                max_seconds=self.command_timeout, silence_seconds=self.silence_seconds
            )
            if wake_tail:
                # The wake word and the command were spoken in one breath;
                # the words after the phrase were captured in the wake listen.
                command = f"{wake_tail} {command}".strip() if command else wake_tail
                wake_tail = ""
            if not command:
                self.on_state_change("idle")
                self.speaker.say(random.choice(_MISHEARD_PHRASES))
                return

            self.on_state_change("thinking")
            interrupted = False
            try:
                # Try routing built-in commands first
                reply = self.router.route(command)
                if reply is not None:
                    self.on_state_change("speaking")
                    interrupted = self._speak_interruptible(reply, stop_check)
                else:
                    # The intent gate: only answer if the utterance is
                    # actually addressed to Emma. Its verdict is consumed
                    # silently - never spoken - so a stray sentence that
                    # just happened to follow the wake word goes unnoticed.
                    if self.judge_enabled:
                        judgment = self.client.judge(command)
                        if not judgment.get("should_respond", True):
                            logger.info(
                                "Intent gate waved off the wake-up: %s",
                                judgment.get("intent", "") or "not addressed to Emma",
                            )
                            continue  # back to capturing, without a peep

                    self.on_state_change("speaking")
                    interrupted = self._stream_reply(
                        command,
                        acknowledge=acknowledge,
                        stop_check=stop_check,
                    )
            except httpx.ConnectError:
                # The common case: ./run.sh isn't running. One clean warning
                # line beats a full traceback per command, and Emma says so.
                logger.warning(
                    "Can't reach Emma's backend at %s - make sure `./run.sh` is "
                    "running. Listening for the wake word again.",
                    self.client.base_url,
                )
                self._apologize(_UNREACHABLE_PHRASE)
                return
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                logger.warning("Voice request/routing failed: %s", exc)
                self._apologize()
                return
            except Exception:  # noqa: BLE001 - surface any backend problem by voice
                logger.exception("Voice request/routing failed")
                self._apologize()
                return

            acknowledge = False  # only the first turn of a wake gets an "mm-hmm?"
            if not interrupted:
                return  # finished speaking uninterrupted -> back to wake-word idle
            # Interrupted: Emma was cut off, loop straight into a fresh command.
            logger.info("Barge-in: Emma was interrupted mid-reply; listening again.")

    def _apologize(self, phrase: Optional[str] = None) -> None:
        """Return to idle and say a short apology, never killing the loop."""
        self.on_state_change("idle")
        try:
            self.speaker.say(phrase or _ERROR_PHRASE)
        except Exception:  # noqa: BLE001 - the backend is already down
            logger.exception("Couldn't speak the apology; continuing anyway")

    def __init__(
        self,
        backend_url: str,
        wake_word: str = "hey emma",
        local_only: bool = True,
        vosk_model_path: Optional[str] = None,
        input_device: Optional[str] = None,
        tts_rate: int = 175,
        tts_voice: Optional[str] = None,
        tts_engine: str = "auto",
        piper_model_path: Optional[str] = None,
        piper_length_scale: Optional[float] = None,
        piper_noise_scale: float = 0.667,
        piper_noise_w_scale: float = 0.8,
        piper_volume: float = 1.0,
        piper_speaker_id: Optional[int] = None,
        chatterbox_reference_wav: Optional[str] = None,
        chatterbox_variant: str = "turbo",
        chatterbox_auto_fallback: bool = True,
        command_timeout: float = 8.0,
        silence_seconds: float = 1.2,
        judge_enabled: bool = True,
        stream_min_chars: int = 15,
        stream_max_chars: int = 320,
        on_state_change: Optional[Callable[[str], None]] = None,
        speak_acknowledgement: bool = True,
        barge_in: bool = True,
    ):
        self.client = VoiceBackendClient(backend_url, local_only=local_only)
        self.speaker = Speaker(
            rate=tts_rate,
            voice_hint=tts_voice,
            engine=tts_engine,
            piper_model_path=piper_model_path,
            length_scale=piper_length_scale,
            noise_scale=piper_noise_scale,
            noise_w_scale=piper_noise_w_scale,
            volume=piper_volume,
            speaker_id=piper_speaker_id,
            chatterbox_reference_wav=chatterbox_reference_wav,
            chatterbox_variant=chatterbox_variant,
            chatterbox_auto_fallback=chatterbox_auto_fallback,
        )
        logger.info("Emma's voice backend: %s", self.speaker.backend_name)
        self.wake_word = wake_word
        self.command_timeout = command_timeout
        self.silence_seconds = silence_seconds
        self.on_state_change = on_state_change or (lambda state: None)
        self.speak_acknowledgement = speak_acknowledgement
        self.judge_enabled = judge_enabled
        self.stream_min_chars = stream_min_chars
        self.stream_max_chars = stream_max_chars
        # Barge-in only makes sense with a neural voice, which we can
        # interrupt promptly. It also needs a mic that isn't the same audio
        # path as the speaker; on a laptop that's fine. Can be turned off
        # (--no-barge-in) if Emma keeps hearing her own voice.
        self.barge_in = barge_in
        self._barge_lock = threading.RLock()  # Protect barge-in state
        self._current_stop_event: Optional[threading.Event] = None
        self._current_watcher: Optional[threading.Thread] = None

        from .commands import VoiceCommandRouter
        self.router = VoiceCommandRouter(self.client)
        self._persona = self.client.get_persona()

        model = load_model(vosk_model_path)
        self.listener = WakeWordListener(model, wake_word=wake_word, device=input_device)

    def _stream_reply(
        self,
        command: str,
        acknowledge: bool,
        stop_check: Optional[Callable[[], bool]],
    ) -> bool:
        """
        Stream Emma's reply from the backend's SSE endpoint and speak it
        sentence by sentence, so she starts talking while the rest of the
        reply is still being generated. Returns True if she was interrupted
        (caller should capture a new command), False if she finished.
        """
        stop_event = None
        watcher = None
        heard_wake = None
        
        with self._barge_lock:
            if self.barge_in:
                stop_event = threading.Event()
                heard_wake = threading.Event()
                self._current_stop_event = stop_event
                watcher = threading.Thread(
                    target=self._watch_for_barge_in,
                    args=(stop_event, heard_wake, stop_check),
                    name="emma-barge-in",
                    daemon=True,
                )
                self._current_watcher = watcher
                watcher.start()

        interrupted = False
        try:
            if acknowledge and not self.speaker.say(
                random.choice(_ACK_PHRASES), stop_event=stop_event
            ):
                interrupted = True

            if not interrupted:
                accumulator = SentenceAccumulator(
                    min_chars=self.stream_min_chars, max_chars=self.stream_max_chars
                )
                delegation_detected = False
                try:
                    for piece in self.client.chat_stream(
                        command, system=self._persona or None
                    ):
                        for sentence in accumulator.feed(piece):
                            # Check if this sentence indicates delegation
                            if not delegation_detected:
                                sentence_lower = sentence.lower()
                                if any(indicator in sentence_lower for indicator in _DELEGATION_INDICATORS):
                                    delegation_detected = True
                                    # Play subtle thinking sound before continuing
                                    if not self.speaker.say(random.choice(_THINKING_SOUNDS), stop_event=stop_event):
                                        interrupted = True
                                        break
                            
                            if not self.speaker.say(sentence, stop_event=stop_event):
                                interrupted = True
                                break
                        if interrupted:
                            break
                    tail = accumulator.flush() if not interrupted else ""
                    if tail and not interrupted:
                        interrupted = not self.speaker.say(tail, stop_event=stop_event)
                except RuntimeError as exc:
                    # A stream failure mid-reply: apologize rather than go silent.
                    logger.warning("Streaming reply failed: %s", exc)
                    self.on_state_change("idle")
                    try:
                        self.speaker.say(_ERROR_PHRASE, stop_event=stop_event)
                    except Exception:  # noqa: BLE001 - a failing backend must not kill the loop
                        logger.exception("Couldn't even speak the apology")
        finally:
            # Whether Emma finished or was cut off, stand the watcher down and
            # wait for it to release the microphone before we touch it again.
            with self._barge_lock:
                if watcher is not None:
                    if stop_event is not None:
                        stop_event.set()
                    # Wait for watcher to finish (it releases mic on stop_event)
                    watcher.join(timeout=2.0)
                self._current_stop_event = None
                self._current_watcher = None

        if not interrupted:
            return False
        # Only count an interruption as a true barge-in if the wake word was
        # actually heard; a GUI stop doesn't send us back to capturing.
        return heard_wake is not None and heard_wake.is_set()

    def _watch_for_barge_in(
        self,
        stop_event: threading.Event,
        heard_wake: threading.Event,
        stop_check: Optional[Callable[[], bool]],
    ) -> None:
        # Cancel this listener as soon as playback ends on its own, so the
        # thread doesn't linger holding the mic into the next turn.
        def _watch_stop() -> bool:
            return stop_event.is_set() or (stop_check is not None and stop_check())

        if self.listener.wait_for_wake_word(stop_check=_watch_stop)[0]:
            heard_wake.set()
            with self._barge_lock:
                # Also signal the main stop_event to cut playback immediately
                if self._current_stop_event is not None:
                    self._current_stop_event.set()

    def _speak_interruptible(
        self, reply: str, stop_check: Optional[Callable[[], bool]]
    ) -> bool:
        """
        Speak `reply` (used for offline-command acknowledgements). If
        barge-in is enabled, listen for the wake word on a side thread
        while speaking; hearing it cuts Emma off. Returns True if Emma was
        interrupted (caller should capture a new command), False if she
        finished the whole reply.
        """
        if not self.barge_in:
            self.speaker.say(reply)
            return False

        stop_event = threading.Event()
        heard_wake = threading.Event()
        watcher = threading.Thread(
            target=self._watch_for_barge_in,
            args=(stop_event, heard_wake, stop_check),
            name="emma-barge-in",
            daemon=True,
        )
        with self._barge_lock:
            self._current_stop_event = stop_event
            self._current_watcher = watcher
        watcher.start()
        try:
            self.speaker.say(reply, stop_event=stop_event)
        finally:
            with self._barge_lock:
                stop_event.set()
                if watcher is not None:
                    watcher.join(timeout=2.0)
                self._current_stop_event = None
                self._current_watcher = None

        return heard_wake.is_set()
