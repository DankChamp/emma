#!/usr/bin/env bash
# Emma - start the wake-word voice assistant. Requires the backend to
# already be running (./run.sh in another terminal) and setup.sh to have
# been run at least once. First time only, also see the setup steps at
# the top of emma_voice.py (audio deps + downloading a Vosk model).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "No virtual environment found. Run ./setup.sh first."
    exit 1
fi

exec .venv/bin/python emma_voice.py "$@"
