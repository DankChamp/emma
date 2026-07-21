---
title: Emma
emoji: 🌸
colorFrom: purple
colorTo: pink
sdk: gradio
sdk_version: 6.20.0
python_version: "3.12"
app_file: app.py
pinned: false
---

# Emma

Personal assistant backend (FastAPI + Telegram bot) running inside a free
Gradio Space. The Gradio page at `/gradio` is just a landing card — the real
app is the FastAPI instance:

- Web UI: `/ui/`
- Health: `/status`

Databases are restored from / backed up to a private HF Dataset
(`EMMA_HF_BACKUP_REPO` secret) because Space disks are ephemeral.
