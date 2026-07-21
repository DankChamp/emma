from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class BusyState:
    is_busy: bool
    note: Optional[str] = None          # e.g. "coding session", "meeting"
    since: Optional[datetime] = None


@dataclass
class NotifyContact:
    id: Optional[int]
    name: str
    busy_message: str    # what to tell them while you're busy, e.g. "I'm busy right now, I'll message you after."
    free_message: Optional[str] = None   # optional message to send when you become free again
    created_at: datetime = field(default_factory=datetime.utcnow)
