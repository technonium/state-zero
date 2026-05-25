import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from pipeline import WHOOPPipeline


IST = ZoneInfo("Asia/Kolkata")


class NextCronSlotIstTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = WHOOPPipeline.__new__(WHOOPPipeline)

    def _slot(self, *args) -> str:
        return self.pipeline._next_cron_slot_ist(datetime(*args, tzinfo=IST))

    def test_just_before_slot_boundary_reports_that_slot(self):
        self.assertEqual(self._slot(2026, 5, 25, 10, 29, 0), "10:30 IST")
        self.assertEqual(self._slot(2026, 5, 25, 10, 29, 59), "10:30 IST")

    def test_at_slot_boundary_reports_following_slot(self):
        self.assertEqual(self._slot(2026, 5, 25, 10, 30, 0), "11:00 IST")
        self.assertEqual(self._slot(2026, 5, 25, 10, 0, 0), "10:30 IST")

    def test_just_after_slot_boundary_reports_following_slot(self):
        self.assertEqual(self._slot(2026, 5, 25, 10, 30, 1), "11:00 IST")
        self.assertEqual(self._slot(2026, 5, 25, 10, 0, 1), "10:30 IST")

    def test_mid_slot_reports_next_boundary(self):
        self.assertEqual(self._slot(2026, 5, 25, 10, 15, 0), "10:30 IST")
        self.assertEqual(self._slot(2026, 5, 25, 10, 45, 0), "11:00 IST")

    def test_hour_rollover(self):
        self.assertEqual(self._slot(2026, 5, 25, 10, 59, 59), "11:00 IST")
        self.assertEqual(self._slot(2026, 5, 25, 23, 59, 59), "00:00 IST")

    def test_accepts_utc_input_and_converts_to_ist(self):
        utc_dt = datetime(2026, 5, 25, 5, 14, 0, tzinfo=ZoneInfo("UTC"))
        # 05:14 UTC = 10:44 IST → next slot 11:00 IST
        self.assertEqual(self.pipeline._next_cron_slot_ist(utc_dt), "11:00 IST")


if __name__ == "__main__":
    unittest.main()
