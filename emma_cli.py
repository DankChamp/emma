#!/usr/bin/env python3
"""
Emma CLI - a thin terminal client over Emma's HTTP API.

This is intentionally just an HTTP client: it never touches core/ directly.
That keeps the CLI, the web UI, and any voice frontend all equally
"just clients" of the same API - no logic duplicated here.

Usage:
    ./emma chat "what should I work on next"
    ./emma task add "Fix WebRTC sync" --project mo-cap --priority high
    ./emma task list
    ./emma task done 3
    ./emma remind "stretch break" --in 20
    ./emma reminders
    ./emma morning
    ./emma night
    ./emma busy "coding session"
    ./emma free
    ./emma status
    ./emma providers list
    ./emma providers set-key groq <key>
    ./emma providers set-model nvidia_nim <model>
    ./emma providers test ollama
    ./emma providers models groq
    ./emma memory long-term
    ./emma memory long-term "who you are..."
    ./emma memory project emma
    ./emma memory project emma "project notes..."
    ./emma memory daily
    ./emma memory daily "today's plan..."
    ./emma persona
    ./emma persona "You are Emma..."
    ./emma telegram status
    ./emma telegram start
    ./emma telegram stop
    ./emma telegram users
    ./emma telegram messages
    ./emma telegram set-label <id> <label> <role>
    ./emma telegram set-chatid <telegram_id> <chat_id>
    ./emma notify send <telegram_id> <message>
    ./emma selfcare diagnostics
    ./emma selfcare repair
    ./emma selfcare updates
    ./emma selfcare apply
    ./emma selfcare deps
    ./emma selfcare changelog
    ./emma selfcare version
"""
import argparse
import json
import os
import subprocess
import sys
import time

import httpx

BASE_URL = os.environ.get("EMMA_API_URL", "http://localhost:8000")


def _request(method: str, path: str, **kwargs):
    try:
        resp = httpx.request(method, f"{BASE_URL}{path}", timeout=60.0, **kwargs)
    except httpx.ConnectError:
        print(f"Can't reach Emma at {BASE_URL}. Is the server running? (./run.sh)", file=sys.stderr)
        sys.exit(1)
    if resp.status_code >= 400:
        print(f"Error {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    return resp.json()


def _print(data) -> None:
    if isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, indent=2))


# ── Busy Mode ──

def cmd_busy(args):
    payload = {"note": args.note} if args.note else {}
    _print(_request("POST", "/status/busy", json=payload))


def cmd_free(args):
    _print(_request("POST", "/status/free"))


def cmd_status(args):
    data = _request("GET", "/status")
    if data.get("is_busy"):
        note = f" - {data['note']}" if data.get("note") else ""
        print(f"● Busy{note}")
    else:
        print("● Free")


# ── Chat ──

def cmd_chat(args):
    persona = ""
    try:
        persona_data = _request("GET", "/memory/persona")
        persona = persona_data.get("text", "")
    except SystemExit:
        pass
    payload = {"message": args.message, "task_type": args.task_type}
    if persona:
        payload["system"] = persona
    data = _request("POST", "/chat", json=payload)
    print(data["reply"])
    print(f"\n(via {data['provider']}/{data['model']})", file=sys.stderr)


# ── Providers ──

def cmd_providers_list(args):
    data = _request("GET", "/settings/providers")
    for p in data:
        dot = "●" if p.get("available") else "○"
        print(f"  {dot} {p['name']:20s} model: {p.get('default_model', '?')}")


def cmd_providers_set_key(args):
    _print(_request("POST", f"/settings/providers/{args.provider}/key", json={"api_key": args.key}))


def cmd_providers_set_model(args):
    _print(_request("POST", f"/settings/providers/{args.provider}/model", json={"model": args.model}))


def cmd_providers_test(args):
    data = _request("POST", f"/settings/providers/{args.provider}/test")
    dot = "●" if data.get("available") else "○"
    print(f"  {dot} {args.provider}: {'online' if data.get('available') else 'offline'}")


def cmd_providers_models(args):
    data = _request("GET", f"/settings/providers/{args.provider}/models")
    for m in data.get("models", []):
        print(f"  {m}")


# ── Memory ──

def cmd_memory_long_term(args):
    if args.text:
        _print(_request("POST", "/memory/long-term-text", json={"text": args.text}))
    else:
        data = _request("GET", "/memory/long-term-text")
        print(data.get("text", ""))


def cmd_memory_project(args):
    if not args.project:
        print("Specify a project name.", file=sys.stderr)
        sys.exit(1)
    if args.text:
        _print(_request("POST", "/memory/project-text", json={"project": args.project, "text": args.text}))
    else:
        data = _request("GET", f"/memory/project-text/{args.project}")
        print(data.get("text", ""))


def cmd_memory_daily(args):
    if args.text:
        _print(_request("POST", "/memory/daily-text", json={"text": args.text}))
    else:
        data = _request("GET", "/memory/daily-text")
        print(data.get("text", ""))


# ── Persona ──

def cmd_persona(args):
    if args.text:
        _print(_request("POST", "/memory/persona", json={"text": args.text}))
    else:
        data = _request("GET", "/memory/persona")
        print(data.get("text", ""))


# ── Telegram / Notifications ──

def cmd_telegram_status(args):
    data = _request("GET", "/notifications/bot-status")
    if not data.get("has_token"):
        print("Bot: no token — set TELEGRAM_BOT_TOKEN in .env")
    elif data.get("running"):
        print("Bot: ● running")
    else:
        print("Bot: ○ stopped")


def cmd_telegram_start(args):
    _print(_request("POST", "/notifications/bot/start"))


def cmd_telegram_stop(args):
    _print(_request("POST", "/notifications/bot/stop"))


def cmd_telegram_users(args):
    users = _request("GET", "/notifications/users")
    if not users:
        print("No registered users.")
        return
    for u in users:
        priority = u.get('priority', 'normal')
        notify = '✓' if u.get('notify_on_busy') else ' '
        busy_msg = u.get('busy_message') or '-'
        owner = 'OWNER' if u.get('is_owner') else ''
        print(f"  {u['telegram_id']:12d}  {u['name']:20s}  label={u.get('label',''):15s}  role={u.get('role',''):10s}  priority={priority:8s}  notify={notify}  chat_id={u.get('chat_id') or '-':>10s}  {owner}")


def cmd_telegram_messages(args):
    msgs = _request("GET", "/notifications/messages")
    if not msgs:
        print("No messages.")
        return
    for m in msgs[-20:]:
        print(f"  [{m['timestamp']}] {m['name']} ({m['user_id']}): {m['text']}")


def cmd_telegram_set_label(args):
    _print(_request("POST", "/notifications/users/label", json={"telegram_id": args.id, "label": args.label, "role": args.role}))


def cmd_telegram_set_chatid(args):
    _print(_request("POST", "/notifications/users/chat-id", json={"telegram_id": args.telegram_id, "chat_id": args.chat_id}))


def cmd_notify_send(args):
    data = _request("GET", f"/notifications/bot-status")
    if not data.get("running"):
        print("Bot is not running. Start it with: emma telegram start", file=sys.stderr)
        sys.exit(1)

    mgr_data = _request("GET", "/notifications/users")
    user = next((u for u in mgr_data if u["telegram_id"] == args.telegram_id), None)
    if not user:
        print(f"Telegram user {args.telegram_id} not found.", file=sys.stderr)
        sys.exit(1)

    chat_id = user.get("chat_id")
    if not chat_id:
        print(f"User {args.telegram_id} has no chat_id set.", file=sys.stderr)
        sys.exit(1)

    import httpx as _httpx
    token = _request("GET", "/notifications/bot-status").get("_token", "")
    try:
        token_data = open(os.path.join(os.path.dirname(__file__), ".env")).read()
        import re
        m = re.search(r"TELEGRAM_BOT_TOKEN=(\S+)", token_data)
        if m:
            token = m.group(1)
    except Exception:
        pass

    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": args.message, "parse_mode": "HTML"})
    if r.status_code == 200:
        print("Notification sent.")
    else:
        print(f"Failed: {r.text}", file=sys.stderr)
        sys.exit(1)


# ── Self-Care ──

def cmd_selfcare_diagnostics(args):
    report = _request("GET", "/selfcare/diagnostics")
    print("\n=== DIAGNOSTICS ===")
    print("\n[DATABASES]")
    for db, st in report.get("databases", {}).items():
        print(f"  {'✔' if st == 'ok' else '❌'} {db}: {st}")
    print("\n[CONFIG]")
    for k, v in report.get("config", {}).items():
        ok = v in ("present", "configured")
        print(f"  {'✔' if ok else '❌'} {k}: {v}")
    print("\n[PROVIDERS]")
    for p in report.get("providers", []):
        print(f"  {'✔' if p.get('available') else '❌'} {p['name']}: configured={p.get('configured')}, available={p.get('available')}, model={p.get('default_model')}")
    print("\n[DEPENDENCIES]")
    for d in report.get("dependencies", []):
        print(f"  {'✔' if d.get('installed') else '❌'} {d['name']}{' (v'+d['version']+')' if d.get('installed') else ' (NOT INSTALLED)'}")
    disk = report.get("disk", {})
    total_gb = disk.get("total", 0) / (1024**3)
    free_gb = disk.get("free", 0) / (1024**3)
    print(f"\n[DISK]\n  Total: {total_gb:.1f} GB  Free: {free_gb:.1f} GB")


def cmd_selfcare_repair(args):
    report = _request("POST", "/selfcare/repair")
    print("\n=== REPAIR RESULTS ===")
    for section, items in report.items():
        if isinstance(items, dict):
            print(f"\n[{section.upper()}]")
            for k, v in items.items():
                print(f"  {k}: {v}")


def cmd_selfcare_updates(args):
    info = _request("GET", "/selfcare/updates")
    if info.get("error"):
        print(f"Error: {info['error']}")
        return
    print(f"Local: {info.get('current_commit')}")
    if info.get("has_updates"):
        print(f"Behind by: {info['behind_by']} commit(s)")
        print(f"Latest: {info.get('latest_message')}")
    else:
        print("Up to date.")


def cmd_selfcare_apply(args):
    info = _request("POST", "/selfcare/updates/apply")
    if info.get("success"):
        print(info.get("message", "Updates applied."))
    else:
        print(f"Failed: {info.get('message')}", file=sys.stderr)
        sys.exit(1)


def cmd_selfcare_deps(args):
    info = _request("POST", "/selfcare/updates/deps")
    if info.get("success"):
        print(info.get("message", "Dependencies updated."))
    else:
        print(f"Failed: {info.get('message')}", file=sys.stderr)
        sys.exit(1)


def cmd_selfcare_changelog(args):
    commits = _request("GET", "/selfcare/changelog")
    for c in commits[:10]:
        print(f"  {c['hash'][:8]}  {c['date']}  {c['author']:15s}  {c['message']}")


def cmd_selfcare_version(args):
    info = _request("GET", "/selfcare/version")
    print(f"Branch: {info.get('branch', '?')}")
    print(f"Commit: {info.get('commit', '?')}")
    print(f"Date:   {info.get('date', '?')}")


# ── Voice ──

def cmd_voice_start(args):
    import subprocess as sp
    import sys
    root = os.path.dirname(os.path.abspath(__file__))
    python = os.path.join(root, ".venv", "bin", "python")
    if not os.path.exists(python):
        python = "python3"
    wake = args.wake_word or "hey emma"
    cmd = [python, os.path.join(root, "emma_voice.py"), "--wake-word", wake, "--backend-url", BASE_URL]
    print(f"Starting voice assistant: {' '.join(cmd)}")
    proc = sp.Popen(cmd, stdout=sp.PIPE, stderr=sp.STDOUT, cwd=root)
    print(f"PID: {proc.pid}")
    print("Voice assistant started. Press Ctrl+C to stop.")
    try:
        for line in proc.stdout:
            print(line.decode(errors="replace").rstrip())
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
        print("\nVoice assistant stopped.")


def cmd_voice_stop(args):
    import subprocess as sp
    try:
        r = sp.run(["pkill", "-f", "emma_voice.py"], capture_output=True)
        if r.returncode == 0:
            print("Voice assistant stopped.")
        else:
            print("No voice assistant process found.")
    except FileNotFoundError:
        print("pkill not available. Kill the process manually.")


# ── Contacts (Busy Mode) ──

def cmd_contacts_list(args):
    contacts = _request("GET", "/status/contacts")
    if not contacts:
        print("No contacts.")
        return
    for c in contacts:
        print(f"  {c['name']:20s}  busy msg: {c['busy_message']}")


def cmd_contacts_add(args):
    _print(_request("POST", "/status/contacts", json={"name": args.name, "busy_message": args.message}))


def cmd_contacts_remove(args):
    _print(_request("DELETE", f"/status/contacts/{args.name}"))


# ── Build Parser ──

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="emma", description="Talk to Emma from the terminal.")
    sub = parser.add_subparsers(dest="command", required=True)



    # busy mode
    p = sub.add_parser("busy", help="enter Busy Mode"); p.add_argument("note", nargs="?", default=None); p.set_defaults(func=cmd_busy)
    p = sub.add_parser("free", help="exit Busy Mode"); p.set_defaults(func=cmd_free)
    p = sub.add_parser("status", help="show current Busy Mode status"); p.set_defaults(func=cmd_status)

    # chat
    p = sub.add_parser("chat", help="talk to Emma's AI router directly")
    p.add_argument("message"); p.add_argument("--task-type", default="conversation", choices=["conversation", "coding", "reasoning", "creative", "general"]); p.set_defaults(func=cmd_chat)

    # providers
    prov = sub.add_parser("providers", help="manage AI providers")
    prov_sub = prov.add_subparsers(dest="providers_command", required=True)
    p = prov_sub.add_parser("list", help="list providers and status"); p.set_defaults(func=cmd_providers_list)
    p = prov_sub.add_parser("set-key", help="set API key for a provider")
    p.add_argument("provider", choices=["groq", "nvidia_nim", "local_generic"]); p.add_argument("key"); p.set_defaults(func=cmd_providers_set_key)
    p = prov_sub.add_parser("set-model", help="set default model for a provider")
    p.add_argument("provider", choices=["ollama", "groq", "nvidia_nim", "local_generic"]); p.add_argument("model"); p.set_defaults(func=cmd_providers_set_model)
    p = prov_sub.add_parser("test", help="test connection to a provider")
    p.add_argument("provider", choices=["ollama", "groq", "nvidia_nim", "local_generic"]); p.set_defaults(func=cmd_providers_test)
    p = prov_sub.add_parser("models", help="list available models for a provider")
    p.add_argument("provider", choices=["ollama", "groq", "nvidia_nim", "local_generic"]); p.set_defaults(func=cmd_providers_models)

    # memory
    mem = sub.add_parser("memory", help="read/write Emma's memory")
    mem_sub = mem.add_subparsers(dest="memory_command", required=True)
    p = mem_sub.add_parser("long-term", help="get or set long-term memory"); p.add_argument("text", nargs="?", default=None); p.set_defaults(func=cmd_memory_long_term)
    p = mem_sub.add_parser("project", help="get or set project memory")
    p.add_argument("project"); p.add_argument("text", nargs="?", default=None); p.set_defaults(func=cmd_memory_project)
    p = mem_sub.add_parser("daily", help="get or set daily memory"); p.add_argument("text", nargs="?", default=None); p.set_defaults(func=cmd_memory_daily)

    # persona
    p = sub.add_parser("persona", help="get or set Emma's identity/system prompt")
    p.add_argument("text", nargs="?", default=None); p.set_defaults(func=cmd_persona)

    # telegram
    tg = sub.add_parser("telegram", help="manage the Telegram notification bot")
    tg_sub = tg.add_subparsers(dest="telegram_command", required=True)
    p = tg_sub.add_parser("status", help="show bot status"); p.set_defaults(func=cmd_telegram_status)
    p = tg_sub.add_parser("start", help="start the bot"); p.set_defaults(func=cmd_telegram_start)
    p = tg_sub.add_parser("stop", help="stop the bot"); p.set_defaults(func=cmd_telegram_stop)
    p = tg_sub.add_parser("users", help="list registered users"); p.set_defaults(func=cmd_telegram_users)
    p = tg_sub.add_parser("messages", help="show recent messages"); p.set_defaults(func=cmd_telegram_messages)
    p = tg_sub.add_parser("set-label", help="set label and role for a user")
    p.add_argument("id", type=int); p.add_argument("label"); p.add_argument("role", choices=["friend", "family", "work", "other"]); p.set_defaults(func=cmd_telegram_set_label)
    p = tg_sub.add_parser("set-chatid", help="set chat ID for a user")
    p.add_argument("telegram_id", type=int); p.add_argument("chat_id", type=int); p.set_defaults(func=cmd_telegram_set_chatid)

    # notify
    p = sub.add_parser("notify", help="send a Telegram notification")
    p.add_argument("send", help="send a message to a user"); p.add_argument("telegram_id", type=int); p.add_argument("message"); p.set_defaults(func=cmd_notify_send)

    # selfcare
    sc = sub.add_parser("selfcare", help="diagnostics, repair, updates")
    sc_sub = sc.add_subparsers(dest="selfcare_command", required=True)
    p = sc_sub.add_parser("diagnostics", help="run full diagnostics"); p.set_defaults(func=cmd_selfcare_diagnostics)
    p = sc_sub.add_parser("repair", help="auto-repair issues"); p.set_defaults(func=cmd_selfcare_repair)
    p = sc_sub.add_parser("updates", help="check for git updates"); p.set_defaults(func=cmd_selfcare_updates)
    p = sc_sub.add_parser("apply", help="apply git updates"); p.set_defaults(func=cmd_selfcare_apply)
    p = sc_sub.add_parser("deps", help="update Python dependencies"); p.set_defaults(func=cmd_selfcare_deps)
    p = sc_sub.add_parser("changelog", help="show recent commits"); p.set_defaults(func=cmd_selfcare_changelog)
    p = sc_sub.add_parser("version", help="show version info"); p.set_defaults(func=cmd_selfcare_version)

    # voice
    voice = sub.add_parser("voice", help="manage the voice assistant")
    voice_sub = voice.add_subparsers(dest="voice_command", required=True)
    p = voice_sub.add_parser("start", help="start listening for wake word")
    p.add_argument("--wake-word", default="hey emma"); p.set_defaults(func=cmd_voice_start)
    p = voice_sub.add_parser("stop", help="stop the voice assistant"); p.set_defaults(func=cmd_voice_stop)

    # contacts
    cnt = sub.add_parser("contacts", help="manage busy-mode contacts")
    cnt_sub = cnt.add_subparsers(dest="contacts_command", required=True)
    p = cnt_sub.add_parser("list", help="list contacts"); p.set_defaults(func=cmd_contacts_list)
    p = cnt_sub.add_parser("add", help="add a contact"); p.add_argument("name"); p.add_argument("message"); p.set_defaults(func=cmd_contacts_add)
    p = cnt_sub.add_parser("remove", help="remove a contact"); p.add_argument("name"); p.set_defaults(func=cmd_contacts_remove)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
