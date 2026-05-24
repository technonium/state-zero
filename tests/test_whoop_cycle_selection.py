import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import lookups
from whoop_client import WHOOPClient, WhoopAPIError, WhoopDailyDataPendingError


class WhoopCycleSelectionTests(unittest.IsolatedAsyncioTestCase):
    def _build_client(self, cycles):
        client = WHOOPClient.__new__(WHOOPClient)
        client.local_tz = ZoneInfo("Asia/Kolkata")

        async def _fake_cycles_window(_day):
            return cycles

        async def _fake_fetch_cycles(_start_local, _end_local, _limit):
            return cycles

        client._get_cycles_window = _fake_cycles_window
        client._fetch_cycles = _fake_fetch_cycles
        return client

    async def test_selects_completed_cycle_before_sleep_start_over_newer_in_progress_cycle(self):
        cycles = [
            {
                "id": 1417954589,
                "start": "2026-04-07T20:02:00Z",
                "end": "2026-04-08T17:02:00Z",
                "updated_at": "2026-04-09T03:17:48Z",
                "score_state": "SCORED",
                "score": {"strain": 14.694084},
            },
            {
                "id": 1419977379,
                "start": "2026-04-08T17:02:00Z",
                "end": None,
                "updated_at": "2026-04-09T03:17:48Z",
                "score_state": "SCORED",
                "score": {"strain": 0.40249443},
            },
        ]
        sleep_data = {
            "start": "2026-04-08T17:02:00Z",
            "end": "2026-04-09T03:11:00Z",
        }
        client = self._build_client(cycles)

        selected = await client.get_prior_completed_strain_cycle(date(2026, 4, 9), sleep_data=sleep_data)

        self.assertEqual(selected["id"], 1417954589)
        self.assertEqual(selected["score"]["strain"], 14.694084)

    async def test_selects_long_spanning_cycle_that_started_two_days_earlier(self):
        cycles = [
            {
                "id": 1419977379,
                "start": "2026-04-08T17:02:00Z",
                "end": "2026-04-09T19:32:00Z",
                "updated_at": "2026-04-10T03:42:28Z",
                "score_state": "SCORED",
                "score": {"strain": 11.703202},
            },
            {
                "id": 1422135324,
                "start": "2026-04-09T19:32:00Z",
                "end": None,
                "updated_at": "2026-04-10T03:42:28Z",
                "score_state": "SCORED",
                "score": {"strain": 4.024952},
            },
        ]
        sleep_data = {
            "start": "2026-04-09T19:32:00Z",
            "end": "2026-04-10T03:24:00Z",
        }
        client = self._build_client(cycles)

        selected = await client.get_prior_completed_strain_cycle(date(2026, 4, 10), sleep_data=sleep_data)

        self.assertEqual(selected["id"], 1419977379)
        self.assertEqual(selected["score"]["strain"], 11.703202)

    async def test_ignores_unscored_and_missing_strain_cycles(self):
        cycles = [
            {
                "id": 100,
                "start": "2026-04-08T10:00:00Z",
                "end": "2026-04-08T12:00:00Z",
                "updated_at": "2026-04-08T12:30:00Z",
                "score_state": "PENDING_SCORE",
                "score": {"strain": 8.0},
            },
            {
                "id": 101,
                "start": "2026-04-08T12:00:00Z",
                "end": "2026-04-08T14:00:00Z",
                "updated_at": "2026-04-08T14:30:00Z",
                "score_state": "SCORED",
                "score": {},
            },
            {
                "id": 102,
                "start": "2026-04-08T14:00:00Z",
                "end": "2026-04-08T16:00:00Z",
                "updated_at": "2026-04-08T16:30:00Z",
                "score_state": "SCORED",
                "score": {"strain": 9.75},
            },
        ]
        sleep_data = {
            "start": "2026-04-08T17:02:00Z",
            "end": "2026-04-09T03:11:00Z",
        }
        client = self._build_client(cycles)

        selected = await client.get_prior_completed_strain_cycle(date(2026, 4, 9), sleep_data=sleep_data)

        self.assertEqual(selected["id"], 102)

    async def test_raises_when_no_completed_cycle_exists_before_sleep_start(self):
        cycles = [
            {
                "id": 1422135324,
                "start": "2026-04-09T19:32:00Z",
                "end": None,
                "updated_at": "2026-04-10T03:42:28Z",
                "score_state": "SCORED",
                "score": {"strain": 4.024952},
            },
        ]
        sleep_data = {
            "start": "2026-04-09T19:32:00Z",
            "end": "2026-04-10T03:24:00Z",
        }
        client = self._build_client(cycles)

        with self.assertRaises(WhoopAPIError) as ctx:
            await client.get_prior_completed_strain_cycle(date(2026, 4, 10), sleep_data=sleep_data)

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("No completed strain cycle found before primary sleep start", ctx.exception.message)

    async def test_expands_cycle_search_beyond_initial_window_when_needed(self):
        client = WHOOPClient.__new__(WHOOPClient)
        client.local_tz = ZoneInfo("Asia/Kolkata")

        initial_cycles = [
            {
                "id": 1422135324,
                "start": "2026-04-09T19:32:00Z",
                "end": None,
                "updated_at": "2026-04-10T03:42:28Z",
                "score_state": "SCORED",
                "score": {"strain": 4.024952},
            }
        ]
        expanded_cycles = [
            {
                "id": 1419977379,
                "start": "2026-04-06T17:02:00Z",
                "end": "2026-04-09T19:32:00Z",
                "updated_at": "2026-04-10T03:42:28Z",
                "score_state": "SCORED",
                "score": {"strain": 11.703202},
            }
        ]
        sleep_data = {
            "start": "2026-04-09T19:32:00Z",
            "end": "2026-04-10T03:24:00Z",
        }

        async def _fake_cycles_window(_day):
            return initial_cycles

        async def _fake_fetch_cycles(start_local, end_local, limit):
            return expanded_cycles

        client._get_cycles_window = _fake_cycles_window
        client._fetch_cycles = _fake_fetch_cycles

        selected = await client.get_prior_completed_strain_cycle(date(2026, 4, 10), sleep_data=sleep_data)

        self.assertEqual(selected["id"], 1419977379)
        self.assertEqual(selected["score"]["strain"], 11.703202)

    async def test_get_yesterday_cycle_alias_delegates_to_new_selector(self):
        client = self._build_client([])

        async def _fake_selector(target_date=None, sleep_data=None):
            return {"id": 999, "score": {"strain": 12.3}}

        client.get_prior_completed_strain_cycle = _fake_selector

        selected = await client.get_yesterday_cycle(date(2026, 4, 9))

        self.assertEqual(selected["id"], 999)

    async def test_primary_sleep_waits_for_wake_finalization_window(self):
        client = WHOOPClient.__new__(WHOOPClient)
        client.local_tz = ZoneInfo("Asia/Kolkata")
        client._now_utc = lambda: datetime(2026, 5, 23, 5, 30, tzinfo=timezone.utc)

        async def _fake_get(path, params=None):
            self.assertEqual(path, "/v2/activity/sleep")
            return {
                "records": [
                    {
                        "id": "sleep-1",
                        "nap": False,
                        "start": "2026-05-22T19:40:00Z",
                        "end": "2026-05-23T05:15:00Z",
                        "updated_at": "2026-05-23T05:20:00Z",
                        "score_state": "SCORED",
                        "score": {"sleep_performance_percentage": 43.0},
                    }
                ]
            }

        client.get = _fake_get

        with self.assertRaises(WhoopDailyDataPendingError) as ctx:
            await client.get_last_sleep(date(2026, 5, 23))

        self.assertEqual(ctx.exception.reason, "whoop_finalization_window_open")

    async def test_recovery_must_match_selected_sleep_id(self):
        client = WHOOPClient.__new__(WHOOPClient)
        client.local_tz = ZoneInfo("Asia/Kolkata")
        client._now_utc = lambda: datetime(2026, 5, 23, 8, 0, tzinfo=timezone.utc)

        async def _fake_cycles_window(_day):
            return [{"id": 1514500539}]

        async def _fake_get(path, params=None):
            self.assertEqual(path, "/v2/cycle/1514500539/recovery")
            return {
                "cycle_id": 1514500539,
                "sleep_id": "other-sleep",
                "updated_at": "2026-05-23T06:16:34Z",
                "score_state": "SCORED",
                "score": {"recovery_score": 47.0},
            }

        client._get_cycles_window = _fake_cycles_window
        client.get = _fake_get

        with self.assertRaises(WhoopDailyDataPendingError) as ctx:
            await client.get_today_recovery(
                date(2026, 5, 23),
                sleep_data={"id": "selected-sleep"},
            )

        self.assertEqual(ctx.exception.reason, "whoop_recovery_sleep_mismatch")

    async def test_fetch_whoop_data_returns_provenance_snapshot(self):
        testcase = self

        class _FakeClient:
            local_tz = ZoneInfo("Asia/Kolkata")

            async def get_last_sleep(self, target_dt):
                return {
                    "id": "sleep-1",
                    "start": "2026-05-22T19:40:00Z",
                    "end": "2026-05-23T05:15:00Z",
                    "updated_at": "2026-05-23T06:16:00Z",
                    "score_state": "SCORED",
                    "score": {
                        "sleep_performance_percentage": 87.0,
                        "total_sleep_time_milli": 33_480_000,
                    },
                }

            async def get_prior_completed_strain_cycle(self, target_dt, sleep_data=None):
                return {
                    "id": 1514500539,
                    "start": "2026-05-22T01:10:00Z",
                    "end": "2026-05-22T19:40:00Z",
                    "updated_at": "2026-05-23T06:16:00Z",
                    "score_state": "SCORED",
                    "score": {"strain": 15.121763},
                }

            async def get_today_recovery(self, target_dt, sleep_data=None):
                testcase.assertEqual(sleep_data["id"], "sleep-1")
                return {
                    "cycle_id": 1514500540,
                    "sleep_id": "sleep-1",
                    "updated_at": "2026-05-23T06:16:34Z",
                    "score_state": "SCORED",
                    "score": {"recovery_score": 47.0},
                }

        with patch("lookups.WHOOPClient", return_value=_FakeClient()):
            payload = await lookups._fetch_whoop_data(date(2026, 5, 23))

        snapshot = payload["whoop_snapshot"]
        self.assertEqual(snapshot["provenance_version"], 1)
        self.assertEqual(snapshot["sleep_id"], "sleep-1")
        self.assertEqual(snapshot["recovery_sleep_id"], "sleep-1")
        self.assertEqual(snapshot["strain_cycle_id"], 1514500539)
        self.assertEqual(snapshot["public_values"]["sleep_score_pct"], 87.0)
        self.assertEqual(snapshot["public_values"]["recovery_pct"], 47.0)
        self.assertEqual(snapshot["public_values"]["sleep_hours"], 9.3)


class LookupsNotReadyTests(unittest.TestCase):
    def test_get_whoop_data_maps_missing_pre_sleep_cycle_to_not_ready(self):
        async def _raise_not_ready(_target_date):
            raise WhoopDailyDataPendingError(
                "completed_pre_sleep_cycle_missing",
                "No completed strain cycle found before primary sleep start for 2026-04-10 IST",
            )

        with patch("lookups._fetch_whoop_data", side_effect=_raise_not_ready):
            with self.assertRaises(lookups.WhoopDailyDataNotReady) as ctx:
                lookups.get_whoop_data(date(2026, 4, 10))

        self.assertEqual(ctx.exception.reason, "completed_pre_sleep_cycle_missing")

    def test_main_exits_with_not_ready_code_for_missing_pre_sleep_cycle(self):
        with patch.object(sys, "argv", ["lookups.py", "--date", "2026-04-10"]):
            with patch(
                "lookups.get_whoop_data",
                side_effect=lookups.WhoopDailyDataNotReady("strain cycle missing"),
            ):
                with self.assertRaises(SystemExit) as ctx:
                    lookups.main()

        self.assertEqual(ctx.exception.code, lookups.LOOKUP_EXIT_WHOOP_NOT_READY)

    def test_compatibility_alias_for_old_not_ready_name(self):
        self.assertIs(lookups.WhoopRecoveryNotReady, lookups.WhoopDailyDataNotReady)


if __name__ == "__main__":
    unittest.main()
