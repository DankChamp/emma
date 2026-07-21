from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TelegramUser:
    id: Optional[int]
    telegram_id: int
    name: str
    label: str                 # "Alex" / "Mom" / "Boss" — who they are to you
    role: str                  # "friend" / "family" / "work" / "other"
    can_receive: bool          # can Emma send notifications to this person
    priority: str = "normal"   # "high" / "normal" / "low" — high breaks through busy mode
    notify_on_busy: bool = False       # message this person when you go busy/free
    busy_message: Optional[str] = None  # custom line to send them while you're busy
    free_message: Optional[str] = None  # custom line to send them when you're free again
    is_owner: bool = False     # your own chat — where reminders + high-priority alerts land
    prompt: Optional[str] = None   # free-text context: who they are, your relationship, etc.
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_seen: Optional[datetime] = None
