"""
Task model - one row in tasks.db.

Priorities: low | medium | high
Status:     pending | done
Deadline is optional ISO date or datetime; None = no deadline.
Project ties a task to a project-memory name so "what's left on HyperClutch"
is a single WHERE clause, per the spec's project-scoped design.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

PRIORITIES = ("low", "medium", "high")
STATUSES = ("pending", "done")


@dataclass
class Task:
    id: Optional[int]
    title: str
    project: Optional[str] = None
    priority: str = "medium"
    status: str = "pending"
    deadline: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
