from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.reflexion import is_night_window


class NightWindowTests(unittest.TestCase):
    def test_cross_midnight_window_includes_start_and_excludes_end(self) -> None:
        self.assertTrue(is_night_window("23:00", "06:00", datetime(2026, 11, 24, 23, 0)))
        self.assertTrue(is_night_window("23:00", "06:00", datetime(2026, 11, 25, 5, 59)))
        self.assertFalse(is_night_window("23:00", "06:00", datetime(2026, 11, 25, 6, 0)))
        self.assertFalse(is_night_window("23:00", "06:00", datetime(2026, 11, 24, 12, 0)))

    def test_same_day_window_includes_start_and_excludes_end(self) -> None:
        self.assertTrue(is_night_window("01:00", "03:00", datetime(2026, 11, 24, 1, 0)))
        self.assertTrue(is_night_window("01:00", "03:00", datetime(2026, 11, 24, 2, 59)))
        self.assertFalse(is_night_window("01:00", "03:00", datetime(2026, 11, 24, 3, 0)))
        self.assertFalse(is_night_window("01:00", "03:00", datetime(2026, 11, 24, 0, 59)))


if __name__ == "__main__":
    unittest.main()
