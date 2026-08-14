#!/usr/bin/env python3
"""
Check a reference voice sample for Chatterbox cloning.

Chatterbox needs a ~10-second recording of the voice Emma should sound
like. This tool inspects any WAV you point it at, tells you whether it's
suitable, and prints the exact .env line that enables it:

    python voice/check_reference.py /path/to/your/sample.wav

Requirements for a good clone sample:
  - at least 5 seconds (ideally ~10s) of speech
  - one voice, cleanly recorded (no background voices/music)
  - no clipping (warning only)
Any sample rate works - the engine resamples internally.
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path


def inspect(path: str) -> int:
    p = Path(path).expanduser()
    if not p.is_file():
        print(f"Not found: {p}")
        return 1

    if p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as w:
                frames = w.getnframes()
                rate = w.getframerate()
                channels = w.getnchannels()
                sampwidth = w.getsampwidth()
                duration = frames / rate
                if sampwidth == 2:
                    # Optional clipping check on the raw int16 samples.
                    import array

                    raw = w.readframes(min(frames, rate * 20))
                    samples = array.array("h", raw)
                    max_amp = max(abs(s) for s in samples) if samples else 0
                else:
                    max_amp = None
        except Exception as exc:
            print(f"Couldn't read WAV: {exc}")
            return 1
        print(f"File:      {p}")
        print(f"Duration:  {duration:.1f}s ({channels} ch, {rate} Hz, {sampwidth * 8}-bit)")
        if rate != 24000:
            print(f"Note:      {rate} Hz is fine - the engine resamples to 24 kHz internally.")
        problems = []
        if duration < 5.0:
            problems.append(f"only {duration:.1f}s - Chatterbox requires >5s (aim for ~10s)")
        if duration < 10.0:
            problems.append("under 10s - short samples can sound less consistent; ok if clean")
        if duration > 20.0:
            problems.append("over 20s - only the first ~10s are used; no problem, just extra work")
        if channels > 1:
            problems.append("stereo - mono is preferred, but Chatterbox downmixes fine")
        if max_amp is not None and max_amp >= 32000:
            problems.append("clipping detected - re-record with lower gain for a cleaner clone")
        for problem in problems:
            print(f"Warn:      {problem}")
        if problems and duration < 5.0:
            print("\nThis sample can't be used yet - record a longer one.")
            return 1
        print("\nLooks usable. Copy it into the repo and enable it in .env:")
        print(f'    cp "{p}" voice/reference/emma.wav')
        print("    VOICE_CHATTERBOX_REFERENCE_WAV=voice/reference/emma.wav")
        return 0

    print(
        f"Only WAV files are supported here ({p.suffix}); convert it first, e.g.:\n"
        f"    ffmpeg -i {p} -ar 24000 -ac 1 voice/reference/emma.wav"
    )
    return 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    return inspect(sys.argv[1])


if __name__ == "__main__":
    sys.exit(main())