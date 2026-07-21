"""
VoiceAssistant - ties the wake-word listener, Emma's backend, and TTS
together into the loop `emma_voice.py` runs:

    IDLE (listening for "hey emma")
      -> wake word heard
    LISTENING (recording the command)
      -> silence detected / command captured
    THINKING (POST /chat to Emma's backend)
      -> reply received
    SPEAKING (TTS reads the reply aloud)
      -> back to IDLE
"""
from __future__ import annotations

import logging
import random
import threading
from typing import Callable, Optional

from .client import VoiceBackendClient
from .tts import Speaker
from .wake_word import WakeWordListener, load_model

logger = logging.getLogger("emma.voice.assistant")

# Short, warm acknowledgement lines so there's audible feedback the wake
# word was heard even before Emma's actual reply comes back - useful
# because the chat call itself can take a second or two. Kept friendly and
# natural to match Emma's feminine voice.
_ACK_PHRASES = ["Yes?", "Mm-hmm?", "I'm here.", "Go ahead.", "I'm listening."]

# Spoken when the mic caught nothing intelligible.
_MISHEARD_PHRASES = [
    "Sorry, I didn't quite catch that.",
    "Hmm, I missed that - could you say it again?",
    "Sorry, could you repeat that for me?",
]

# Spoken when something went wrong talking to the backend.
_ERROR_PHRASE = "Sorry, I ran into a problem handling that. Let's try again in a moment."


class VoiceAssistant:
    def __init__(
        self,
        backend_url: str,
        wake_word: str = "hey emma",
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
        command_timeout: float = 8.0,
        silence_seconds: float = 1.2,
        on_state_change: Optional[Callable[[str], None]] = None,
        speak_acknowledgement: bool = True,
        barge_in: bool = True,
    ):
        self.client = VoiceBackendClient(backend_url)
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
        )
        logger.info("Emma's voice backend: %s", self.speaker.backend_name)
        self.wake_word = wake_word
        self.command_timeout = command_timeout
        self.silence_seconds = silence_seconds
        self.on_state_change = on_state_change or (lambda state: None)
        self.speak_acknowledgement = speak_acknowledgement
        # Barge-in only makes sense with the neural (Piper) voice, which we
        # can interrupt promptly. It also needs a mic that isn't the same
        # audio path as the speaker; on a laptop that's fine. Can be turned
        # off (--no-barge-in) if Emma keeps hearing her own voice.
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
        if not heard or (stop_check is not None and stop_check()):
            return

        # Once woken, stay in a tight ask -> answer -> (maybe interrupted) ->
        # ask again loop. A barge-in during Emma's reply drops us straight
        # back into capturing a new command, so the user doesn't have to say
        # the wake word again just to correct or redirect her mid-sentence.
        acknowledge = self.speak_acknowledgement
        while stop_check is None or not stop_check():
            self.on_state_change("listening")
            if acknowledge:
                self.speaker.say(random.choice(_ACK_PHRASES))
            acknowledge = False  # only the first turn of a wake gets an "mm-hmm?"

            command = self.listener.capture_command(
                max_seconds=self.command_timeout, silence_seconds=self.silence_seconds
            )
            if not command:
                self.on_state_change("idle")
                self.speaker.say(random.choice(_MISHEARD_PHRASES))
                return

            self.on_state_change("thinking")
            try:
                # Try routing built-in commands first
                reply = self.router.route(command)
                if reply is None:
                    # Fall back to LLM chat response, injecting persona
                    reply = self.client.chat(command, system=self._persona or None)
            except Exception:  # noqa: BLE001 - surface any backend problem by voice
                logger.exception("Voice request/routing failed")
                self.on_state_change("idle")
                self.speaker.say(_ERROR_PHRASE)
                return

            self.on_state_change("speaking")
            interrupted = self._speak_interruptible(reply, stop_check)
            if not interrupted:
                return  # finished speaking uninterrupted -> back to wake-word idle
            # Interrupted: Emma was cut off, loop straight into a fresh command.
            logger.info("Barge-in: Emma was interrupted mid-reply; listening again.")

    def _speak_interruptible(
        self, reply: str, stop_check: Optional[Callable[[], bool]]
    ) -> bool:
        """
        Speak `reply`. If barge-in is enabled, listen for the wake word on a
        side thread while speaking; hearing it cuts Emma off. Returns True if
        Emma was interrupted (caller should capture a new command), False if
        she finished the whole reply.
        """
        if not self.barge_in:
            self.speaker.say(reply)
            return False

        stop_event = threading.Event()
        heard_wake = threading.Event()

        def _watch() -> None:
            # Cancel this listener as soon as playback ends on its own, so the
            # thread doesn't linger holding the mic into the next turn.
            def _watch_stop() -> bool:
                return stop_event.is_set() or (stop_check is not None and stop_check())

            if self.listener.wait_for_wake_word(stop_check=_watch_stop):
                heard_wake.set()
                stop_event.set()  # cut playback immediately

        watcher = threading.Thread(target=_watch, name="emma-barge-in", daemon=True)
        watcher.start()
        try:
            self.speaker.say(reply, stop_event=stop_event)
        finally:
            # Whether Emma finished or was cut off, stand the watcher down and
            # wait for it to release the microphone before we touch it again.
            stop_event.set()
            watcher.join(timeout=2.0)

        return heard_wake.is_set()
