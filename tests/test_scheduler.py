"""Tests for Emma scheduler and reminders."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from main import _local_to_utc, _utc_now_naive, _auto_build_utc_cron
from core.schedule import TimetableManager
from core.reminders import ReminderManager
from core.busy_mode import BusyModeManager
from core.timeutil import local_now, local_today


class TestTimeConversion:
    """Test timezone conversion utilities."""

    def test_local_to_utc(self):
        """Test converting local time to UTC."""
        # This depends on the system timezone, so we test the function exists
        dt = datetime(2024, 1, 15, 12, 0, 0)
        result = _local_to_utc(dt)
        assert isinstance(result, datetime)
        assert result.tzinfo is None  # Naive UTC

    def test_utc_now_naive(self):
        """Test getting current UTC as naive datetime."""
        result = _utc_now_naive()
        assert isinstance(result, datetime)
        assert result.tzinfo is None

    def test_auto_build_utc_cron(self):
        """Test auto-build cron time conversion."""
        hour, minute = _auto_build_utc_cron()
        assert isinstance(hour, int)
        assert isinstance(minute, int)
        assert 0 <= hour <= 23
        assert 0 <= minute <= 59


class TestTimetableManager:
    """Test TimetableManager."""

    @pytest.fixture
    def timetable(self, temp_dir):
        """Create a timetable manager with temp database."""
        db_path = temp_dir / "schedule.db"
        mock_router = MagicMock()
        return TimetableManager(str(db_path), ai_router=mock_router)

    def test_list_day_empty(self, timetable):
        """Test listing empty day."""
        day = local_today()
        blocks = timetable.list_day(day)
        assert isinstance(blocks, list)
        assert len(blocks) == 0

    def test_current_busy_block_none(self, timetable):
        """Test current busy block when none scheduled."""
        now = local_now()
        block = timetable.current_busy_block(now)
        assert block is None


class TestReminderManager:
    """Test ReminderManager."""

    @pytest.fixture
    def reminder_manager(self, temp_dir):
        """Create a reminder manager with temp database."""
        db_path = temp_dir / "reminders.db"
        mock_notifications = MagicMock()
        mock_busy_mode = MagicMock()
        mock_busy_mode.get_state.return_value = MagicMock(is_busy=False)
        return ReminderManager(str(db_path), notifications=mock_notifications, busy_mode=mock_busy_mode)

    @pytest.mark.asyncio
    async def test_create_reminder(self, reminder_manager):
        """Test creating a reminder."""
        from datetime import datetime
        trigger_at = local_now() + timedelta(minutes=5)
        reminder = reminder_manager.create(
            message="Test reminder",
            trigger_at=trigger_at,
        )
        assert reminder is not None
        assert isinstance(reminder, dict)
        assert "id" in reminder

    @pytest.mark.asyncio
    async def test_list_reminders(self, reminder_manager):
        """Test listing reminders."""
        reminders = reminder_manager.list()
        assert isinstance(reminders, list)


class TestBusyModeManager:
    """Test BusyModeManager."""

    @pytest.fixture
    def busy_mode(self, temp_dir):
        """Create a busy mode manager with temp database."""
        db_path = temp_dir / "busy_mode.db"
        return BusyModeManager(str(db_path))

    @pytest.mark.asyncio
    async def test_get_state_initial(self, busy_mode):
        """Test initial state is free."""
        state = busy_mode.get_state()
        assert state.is_busy is False

    @pytest.mark.asyncio
    async def test_go_busy_and_free(self, busy_mode):
        """Test going busy and free."""
        await busy_mode.go_busy(note="Working")
        state = busy_mode.get_state()
        assert state.is_busy is True
        assert state.note == "Working"
        
        await busy_mode.go_free()
        state = busy_mode.get_state()
        assert state.is_busy is False