#!/usr/bin/env python3
"""
Emma Voice - wake-word ("hey emma") voice front end for Emma.

This is a thin client, same as emma_cli.py: it talks to Emma's HTTP API
and does audio in/out, but has no business logic of its own. Run the
backend first (`./run.sh`), then this in another terminal (`./run_voice.sh`).

One-time setup:
    1. pip install -r requirements.txt   (vosk, sounddevice, piper-tts, pyttsx3)
    2. Linux only: sudo apt install libportaudio2   (espeak-ng only if you
       want the robotic fallback voice)
    3. Download a Vosk model: https://alphacephei.com/vosk/models
       (vosk-model-small-en-us-0.15 is a good ~40MB starting point)
       Unzip it, then set VOICE_VOSK_MODEL_PATH in .env to the folder path.
    4. Give Emma her voice: python voice/download_voice.py
       (fetches a natural feminine neural voice into voice/models/)

Usage:
    ./run_voice.sh
    python emma_voice.py                     # same thing, directly
    python emma_voice.py --wake-word "hey jarvis"
    python emma_voice.py --list-devices       # find your mic's device name/index
    python emma_voice.py --list-voices        # show installed neural + system voices
    python emma_voice.py --engine piper --length-scale 1.1   # tune the voice
"""
import argparse
import logging
import sys

from config import get_settings


def _list_input_devices() -> None:
    from voice.wake_word import _require_sounddevice  # noqa: SLF001 - CLI helper, not public API

    sd = _require_sounddevice()
    print("Available input devices (use the name or index with --device):\n")
    for idx, device in enumerate(sd.query_devices()):
        if device.get("max_input_channels", 0) > 0:
            print(f"  [{idx}] {device['name']}")


def _list_voices() -> None:
    from voice.tts import Speaker

    voices = Speaker.list_voices()
    if not voices:
        print("No TTS voices found yet.")
        print("Get a natural feminine voice with:  python voice/download_voice.py")
        return

    piper = [v for v in voices if v.get("engine") == "piper"]
    system = [v for v in voices if v.get("engine") != "piper"]

    if piper:
        print("Piper neural voices (natural; selected automatically, or set VOICE_PIPER_MODEL_PATH):\n")
        for voice in piper:
            print(f"  {voice['name']}")
        print()
    else:
        print("No Piper neural voice installed yet.")
        print("Get a natural feminine voice with:  python voice/download_voice.py\n")

    if system:
        print("System voices (used only as a fallback; match with --voice substring):\n")
        for voice in system:
            print(f"  {voice['name']}  ({voice['id']})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Emma's wake-word voice front end.")
    parser.add_argument("--wake-word", help='Override the wake phrase (default: from .env, "hey emma")')
    parser.add_argument("--backend-url", help="Override Emma's backend URL")
    parser.add_argument("--device", help="Microphone device name or index")
    parser.add_argument("--engine", choices=["auto", "piper", "pyttsx3"], help="TTS engine (default: from .env, 'auto')")
    parser.add_argument("--piper-model", help="Piper voice model name or .onnx path (default: auto-pick feminine)")
    parser.add_argument("--length-scale", type=float, help="Piper pacing: 1.0 natural, >1 slower, <1 faster")
    parser.add_argument("--voice", help="Fallback system-voice name substring (pyttsx3 only)")
    parser.add_argument("--no-ack", action="store_true", help="Skip the short spoken acknowledgement")
    parser.add_argument(
        "--no-barge-in",
        action="store_true",
        help="Disable interrupting Emma by saying the wake word while she's speaking",
    )
    parser.add_argument("--list-devices", action="store_true", help="List microphones and exit")
    parser.add_argument("--list-voices", action="store_true", help="List installed TTS voices and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.list_devices:
        _list_input_devices()
        return 0
    if args.list_voices:
        _list_voices()
        return 0

    settings = get_settings()

    from voice.assistant import VoiceAssistant
    from voice.wake_word import VoiceDependencyError

    wake_word = args.wake_word or settings.voice_wake_word
    print(f'Listening for "{wake_word}"... (Ctrl+C to stop)')

    try:
        assistant = VoiceAssistant(
            backend_url=args.backend_url or settings.voice_backend_url,
            wake_word=wake_word,
            vosk_model_path=settings.voice_vosk_model_path,
            input_device=args.device or settings.voice_input_device,
            tts_rate=settings.voice_tts_rate,
            tts_voice=args.voice or settings.voice_tts_voice,
            tts_engine=args.engine or settings.voice_tts_engine,
            piper_model_path=args.piper_model or settings.voice_piper_model_path,
            piper_length_scale=args.length_scale if args.length_scale is not None else settings.voice_piper_length_scale,
            piper_noise_scale=settings.voice_piper_noise_scale,
            piper_noise_w_scale=settings.voice_piper_noise_w_scale,
            piper_volume=settings.voice_piper_volume,
            piper_speaker_id=settings.voice_piper_speaker_id,
            command_timeout=settings.voice_command_timeout_seconds,
            silence_seconds=settings.voice_silence_seconds,
            speak_acknowledgement=not args.no_ack,
            barge_in=settings.voice_barge_in and not args.no_barge_in,
            on_state_change=lambda state: print(f"[{state}]"),
        )
    except VoiceDependencyError as exc:
        print(f"\nCan't start voice mode yet: {exc}", file=sys.stderr)
        return 1

    try:
        assistant.run_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
