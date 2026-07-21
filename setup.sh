#!/usr/bin/env bash
# Emma - one-time setup. Run this once, then use ./run.sh every time after.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

if [ ! -f ".env" ]; then
    echo "Creating .env from .env.example (fill in your API keys later if you have them)..."
    cp .env.example .env
fi

chmod +x emma emma_cli.py emma_voice.py run.sh run_gui.sh run_voice.sh voice/download_voice.py 2>/dev/null || true

echo ""
echo "Setup complete."
echo "  Start Emma:      ./run.sh"
echo "  Start the GUI:   ./run_gui.sh    (in a second terminal, once run.sh is up)"
echo "  Start Voice:     ./run_voice.sh  (in a second terminal, once run.sh is up)"
echo "  Talk to Emma:    ./emma task list"
echo "  API docs:        http://localhost:8000/docs (once running)"
echo ""
echo "For a natural feminine voice (recommended), grab a neural voice model:"
echo "  .venv/bin/python voice/download_voice.py"
