# Deploying Emma to Hugging Face Spaces (free, no card)

The Space is the PRIMARY instance: it runs the backend + Telegram bot 24/7
even when your PC is off. The local runit service is a manual backup
(`emma-local start/stop`).

**SDK: Gradio (free).** Docker Spaces are paid on this account, so we use a
free Gradio Space: `contrib/hf-space/app.py` imports Emma's FastAPI app
(which restores the databases first) and mounts a tiny Gradio landing page
on it at `/gradio`. Everything else is identical — web UI at `/ui/`,
health at `/status`, Telegram polling in the lifespan.

## One-time setup (browser, ~8 min)

1. **Account**: https://huggingface.co/join — email signup, free, no card.
2. **Token**: Settings → Access Tokens → "Create new token" → type **Write**.
   Copy it (starts with `hf_`).
3. **Dataset repo** (holds DB backups): https://huggingface.co/new-dataset
   → name `emma-data` → **Private** → Create.
4. **Space**: https://huggingface.co/new-space → name `emma` → SDK: **Gradio**
   (free) → template **Blank** → **Private** → hardware "CPU basic · FREE"
   → no persistent storage, no dev mode → Create.
5. **Space secrets**: Space → Settings → Variables and secrets → add as SECRETS:
   - `TELEGRAM_BOT_TOKEN` = (from local .env)
   - `GROQ_API_KEY` = (from local .env)
   - `GROQ_DEFAULT_MODEL` = llama-3.3-70b-versatile
   - `HF_TOKEN` = the write token from step 2
   - `EMMA_HF_BACKUP_REPO` = <your-username>/emma-data

## Push the code (terminal — Claude runs this given the token)

The Space repo gets: `contrib/hf-space/{app.py,README.md,requirements.txt}`
at its root, plus `main.py`, `config.py`, `api/`, `core/`, `web/` from the
repo. Seed the Dataset with the current `data/*.db` first. (Claude stages
this into a temp dir and pushes with `HfApi.upload_folder`; the Dataset seed
is one `upload_folder` on `data/` filtered to `*.db`.)

The Space then builds (installs `requirements.txt` on the Gradio runtime)
and starts `app.py` on port 7860.
URL: `https://<user>-emma.hf.space` (web UI at `/ui/`).

## Keep-alive pinger (stops the ~48 h inactivity sleep)

Free account at https://cron-job.org (or https://uptimerobot.com):
- URL: `https://<user>-emma.hf.space/status`
- Interval: every 10 minutes.

NOTE: the Space is **Private**, so anonymous pings get a 404. Either make the
Space Public (source stays harmless — secrets are not in the repo), or add a
header to the cron job: `Authorization: Bearer <a READ-scoped hf token>`.
Public is simpler; there is nothing sensitive in the code or UI beyond your
tasks — if that bothers you, use the header approach.

## Verify after first boot (Space → Logs)

- `Restored N database(s) from <user>/emma-data`  ← MUST appear; if it says
  "HF restore FAILED", uploads are auto-disabled to protect the snapshot —
  fix the secret/repo name and Restart the Space.
- Telegram: send the bot a message.
- Create a reminder due in 2 min via `/ui/`; confirm it arrives on Telegram.
- Settings → Restart Space; confirm data survived (tasks still there).

## Gotchas

- Only ONE Telegram poller per token: pause the Space before
  `emma-local start` (the script probes `EMMA_SPACE_URL` and refuses if up).
- Settings saved via the web UI write to the Space's ephemeral `.env` and
  are lost on restart, and Space SECRETS override them anyway — change
  durable settings in Space secrets, not the UI, when on the Space.
- Backups: one commit every 10 min while the Space runs. If the private
  dataset ever gets bulky, squash history:
  `api.super_squash_history(repo_id=..., repo_type='dataset')`.
- Local .env additions for emma-local:
  `HF_TOKEN=...`, `EMMA_HF_BACKUP_REPO=<user>/emma-data`,
  `EMMA_SPACE_URL=https://<user>-emma.hf.space`
