"""
VoiceCommandRouter - parses speech transcript for direct actions.
If a command matches, performs the API call and returns a spoken acknowledgment.
Otherwise returns None to fall back to the default LLM chat response.
"""
from __future__ import annotations

import re
from typing import Optional

from .client import VoiceBackendClient


class VoiceCommandRouter:
    def __init__(self, client: VoiceBackendClient):
        self.client = client

    def route(self, transcript: str) -> Optional[str]:
        """
        Check if the transcript matches any built-in offline commands.
        Returns the text to speak if handled, or None if it should go to LLM chat.
        """
        text = transcript.strip().lower()
        if not text:
            return None

        # 1. Busy Mode toggles
        if re.search(r"\b(?:go|set|i'm|i am) free\b", text):
            try:
                self.client.go_free()
                return "Understood. I have set your status to free."
            except Exception as e:
                return f"Sorry, I failed to update status to free: {e}"

        busy_match = re.search(r"\b(?:go|set|i'm|i am) busy(?:\s+with|\s+doing|\s+on)?\s+(.+)$", text)
        if busy_match:
            note = busy_match.group(1).strip()
            try:
                self.client.go_busy(note)
                return f"Understood. I've set your status to busy with note: {note}."
            except Exception as e:
                return f"Sorry, I failed to update status to busy: {e}"

        if re.search(r"\b(?:go|set|i'm|i am) busy\b", text):
            try:
                self.client.go_busy()
                return "Understood. I have set your status to busy."
            except Exception as e:
                return f"Sorry, I failed to update status to busy: {e}"

        # 2. Status check
        if re.search(r"\b(?:what's|what is) (?:my )?status\b", text) or re.search(r"\bam i busy\b", text):
            try:
                status = self.client.get_status()
                is_busy = status.get("is_busy", False)
                note = status.get("note")
                if is_busy:
                    return f"You are currently busy. Note: {note}" if note else "You are currently busy."
                else:
                    return "You are currently free."
            except Exception as e:
                return f"Sorry, I couldn't fetch your status: {e}"

        # 3. Add task
        task_match = re.search(r"\b(?:add|create) task\s+(.+)$", text)
        if task_match:
            title = task_match.group(1).strip()
            try:
                self.client.create_task(title=title)
                return f"I've added the task: {title}."
            except Exception as e:
                return f"Sorry, I couldn't create that task: {e}"

        # 4. List tasks
        if re.search(r"\b(?:list|what are|show) (?:my )?tasks\b", text):
            try:
                tasks = self.client.list_tasks()
                # filter out completed if needed, but list_tasks returns active tasks
                active_tasks = [t for t in tasks if t.get("status") != "completed"]
                if not active_tasks:
                    return "You have no active tasks right now."
                titles = [t["title"] for t in active_tasks]
                if len(titles) == 1:
                    return f"You have one active task: {titles[0]}."
                else:
                    task_list = ", ".join(titles[:-1]) + f", and {titles[-1]}"
                    return f"Here are your active tasks: {task_list}."
            except Exception as e:
                return f"Sorry, I couldn't retrieve your tasks: {e}"

        # 5. Create reminder
        reminder_match = re.search(r"\bremind me to\s+(.+?)\s+in\s+(\d+)\s+minutes?\b", text)
        if reminder_match:
            message = reminder_match.group(1).strip()
            minutes = int(reminder_match.group(2))
            try:
                self.client.create_reminder(message, minutes)
                return f"Got it. I will remind you to {message} in {minutes} minutes."
            except Exception as e:
                return f"Sorry, I couldn't set that reminder: {e}"

        # 6. Memory save: "remember that [key] is [value]" or "remember preference [key] is [value]"
        memory_match = re.search(r"\b(?:remember|save)(?:\s+that)?\s+(?:(person|preference|habit|fact)\s+)?(.+?)\s+is\s+(.+)$", text)
        if memory_match:
            category = memory_match.group(1) or "fact"
            key_raw = memory_match.group(2).strip()
            value = memory_match.group(3).strip()

            # Normalize key to lowercase with underscores
            key = re.sub(r"\s+", "_", key_raw).lower()
            try:
                self.client.save_memory(category, key, value)
                return f"I've committed that to your long-term memory under category {category}."
            except Exception as e:
                return f"Sorry, I failed to save that memory: {e}"

        return None
