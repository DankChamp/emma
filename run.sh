#!/usr/bin/env bash
# Emma - start the server. Run ./setup.sh first if you haven't already.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "No virtual environment found. Run ./setup.sh first."
    exit 1
fi

HOST="${EMMA_HOST:-0.0.0.0}"
PORT="${EMMA_PORT:-8000}"

if [ "${1:-}" = "--dev" ]; then
    exec .venv/bin/uvicorn main:app --host "$HOST" --port "$PORT" --reload
else
    exec .venv/bin/uvicorn main:app --host "$HOST" --port "$PORT"
fi
