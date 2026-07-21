from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class TimeBlock:
    """A single scheduled block in a day's timetable.

    `busy=True` means this block makes you unavailable — it's what the bot
    reads to work out when you're next free. Non-busy blocks (breaks, "free"
    slots) are kept so the schedule reads naturally but don't gate contact.
    """
    id: Optional[int]
    day: date
    start: datetime
    end: datetime
    title: str
    busy: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
