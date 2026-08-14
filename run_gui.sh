#!/usr/bin/env bash
# Emma - open the desktop control panel (chat, memory, schedule, providers...).
# Requires the backend to already be running (./run.sh in another terminal)
# and setup.sh to have been run at least once.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "No virtual environment found. Run ./setup.sh first."
    exit 1
fi

exec .venv/bin/python -m gui.app "$@"