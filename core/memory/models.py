"""
Memory models.

Four distinct tiers, matching the spec exactly:
- LongTermMemory: people, preferences, habits - persists forever
- ProjectMemory: scoped to one project (Emma, HyperClutch, Broniqlabs...)
- DailyMemory: today's working context, expires/rolls over each day
- ConversationMemory: recent turns only, short rolling window

Keeping these as separate tables (not one blob) is what lets Emma query
"what do I know about Circuit's preferences" without dragging in today's
grocery reminder.
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional


@dataclass
class LongTermMemory:
    id: Optional[int]
    category: str          # "person" | "preference" | "habit" | "fact"
    key: str                # e.g. "coding_partner_name"
    value: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProjectMemory:
    id: Optional[int]
    project: str            # "emma" | "hyperclutch" | "broniqlabs" | ...
    key: str
    value: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class DailyMemory:
    id: Optional[int]
    day: date
    key: str
    value: str
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ConversationTurn:
    id: Optional[int]
    session_id: str
    role: str                # "user" | "assistant"
    content: str
    created_at: datetime = field(default_factory=datetime.utcnow)
