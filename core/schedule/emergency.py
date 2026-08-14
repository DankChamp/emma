"""Emergency task handling — reshuffle today's schedule around an urgent item."""

from datetime import datetime, time, timedelta
from typing import Optional

from .models import TimeBlock

_MIN_SLOT_MINUTES = 30
_DEFAULT_EMERGENCY_DURATION = 60


def find_emergency_slot(blocks: list[TimeBlock], duration_minutes: int = _DEFAULT_EMERGENCY_DURATION,
                        now: Optional[datetime] = None) -> Optional[tuple[datetime, datetime]]:
    """Find the best slot for an emergency task in today's schedule.

    Looks for the first free gap after now that fits the duration.
    Returns (start, end) or None if no gap found.
    """
    now = now or datetime.now()
    if not blocks:
        start = now.replace(second=0, microsecond=0) + timedelta(minutes=15)
        start = start.replace(minute=(start.minute // 15) * 15)
        return start, start + timedelta(minutes=duration_minutes)

    busy_blocks = [b for b in blocks if b.busy and b.end > now]
    busy_blocks.sort(key=lambda b: b.start)

    cursor = now
    for block in busy_blocks:
        if block.start > cursor:
            gap_minutes = (block.start - cursor).total_seconds() / 60
            if gap_minutes >= duration_minutes:
                return cursor, cursor + timedelta(minutes=duration_minutes)
        cursor = max(cursor, block.end)

    day_end = datetime.combine(now.date(), time(23, 0))
    if day_end > cursor:
        gap_minutes = (day_end - cursor).total_seconds() / 60
        if gap_minutes >= duration_minutes:
            return cursor, cursor + timedelta(minutes=duration_minutes)

    return None


def make_emergency_room(blocks: list[TimeBlock], duration_minutes: int = _DEFAULT_EMERGENCY_DURATION,
                        now: Optional[datetime] = None) -> Optional[tuple[datetime, datetime, str]]:
    """Try to make room by compressing/shifting blocks.

    Returns (start, end, note) or None if impossible.
    """
    now = now or datetime.now()
    future_blocks = [b for b in blocks if b.busy and b.end > now]
    future_blocks.sort(key=lambda b: b.start)

    if not future_blocks:
        start = now.replace(second=0, microsecond=0) + timedelta(minutes=15)
        start = start.replace(minute=(start.minute // 15) * 15)
        return start, start + timedelta(minutes=duration_minutes), ""

    prev_end = now
    for block in future_blocks:
        block_duration = (block.end - block.start).total_seconds() / 60
        if block_duration >= duration_minutes + _MIN_SLOT_MINUTES:
            note = f"'{block.title}' shortened to make room"
            return block.start, block.start + timedelta(minutes=duration_minutes), note

        # Gap between the previous busy block's end and this one's start.
        # Using the *previous* block (not the first) keeps the gap from
        # including earlier blocks' durations, which could otherwise place
        # the emergency inside a busy block.
        gap_before = (block.start - prev_end).total_seconds() / 60
        if gap_before >= _MIN_SLOT_MINUTES:
            combined = block_duration + gap_before
            if combined >= duration_minutes:
                note = "Blocks compressed to make room"
                start = block.start - timedelta(minutes=min(gap_before, duration_minutes))
                return start, start + timedelta(minutes=duration_minutes), note
        prev_end = max(prev_end, block.end)

    last_block = future_blocks[-1]
    day_end = datetime.combine(now.date(), time(23, 0))
    if last_block.end < day_end:
        return last_block.end, last_block.end + timedelta(minutes=duration_minutes), "Added after last block"

    return None


def insert_emergency(blocks: list[TimeBlock], title: str,
                     duration_minutes: int = _DEFAULT_EMERGENCY_DURATION,
                     now: Optional[datetime] = None) -> tuple[list[TimeBlock], Optional[str]]:
    """Insert an emergency task into the schedule.

    Returns (updated_blocks, note) where note describes what was changed.
    """
    now = now or datetime.now()
    slot = find_emergency_slot(blocks, duration_minutes, now)

    note = None
    if slot is None:
        result = make_emergency_room(blocks, duration_minutes, now)
        if result is None:
            return blocks, "Could not find room for emergency task"
        # make_emergency_room returns (start, end, note).
        start, end, note = result
    else:
        start, end = slot

    emergency_block = TimeBlock(
        id=None,
        day=now.date(),
        start=start,
        end=end,
        title=f"🚨 {title}",
        busy=True,
        created_at=datetime.utcnow(),
    )

    updated = []
    for b in blocks:
        if not b.busy or b.end <= now:
            updated.append(b)
            continue
        if b.end <= start or b.start >= end:
            # Completely before the emergency, or completely after it: keep.
            updated.append(b)
            continue
        # Overlaps the emergency. When a block was "shortened to make room"
        # it must not silently vanish: keep whatever usable tail remains
        # after the emergency instead of deleting the whole task.
        if b.end - end >= timedelta(minutes=_MIN_SLOT_MINUTES):
            updated.append(
                TimeBlock(
                    id=b.id,
                    day=b.day,
                    start=end,
                    end=b.end,
                    title=b.title,
                    busy=True,
                    created_at=b.created_at,
                )
            )
    updated.append(emergency_block)
    updated.sort(key=lambda b: b.start)

    return updated, note or f"'{title}' scheduled from {start.strftime('%H:%M')} to {end.strftime('%H:%M')}"
