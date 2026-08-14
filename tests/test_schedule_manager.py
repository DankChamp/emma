"""
Regression tests for schedule parsing helpers.
"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core.schedule.manager import TimetableManager


class TimetableManagerTest(unittest.TestCase):
    def test_noon_24_hour_time_stays_noon(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = TimetableManager(Path(tmp) / "schedule.db")
            blocks = mgr.set_day(
                date(2026, 8, 13),
                [{"start": "12:30", "end": "13:00", "title": "Lunch", "busy": False}],
            )

        self.assertEqual(blocks[0].start.hour, 12)
        self.assertEqual(blocks[0].start.minute, 30)
        self.assertEqual(blocks[0].end.hour, 13)

    def test_midnight_am_time_stays_midnight(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = TimetableManager(Path(tmp) / "schedule.db")
            blocks = mgr.set_day(
                date(2026, 8, 13),
                [{"start": "12:30 AM", "end": "01:00 AM", "title": "Late", "busy": True}],
            )

        self.assertEqual(blocks[0].start.hour, 0)
        self.assertEqual(blocks[0].start.minute, 30)


if __name__ == "__main__":
    unittest.main()
