import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from pipeline import WHOOPPipeline


class TelegramWaitRevalidationIntervalTests(unittest.TestCase):
    """Covers the env-var-driven interval helper that the Telegram waiter polls on."""

    def setUp(self):
        self.pipeline = WHOOPPipeline.__new__(WHOOPPipeline)

    def test_default_interval_is_15_minutes(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WHOOP_TELEGRAM_WAIT_REVAL_MINUTES", None)
            os.environ.pop("WHOOP_REVALIDATE_BEFORE_PUBLISH", None)
            self.assertEqual(self.pipeline._telegram_wait_revalidation_seconds(), 15 * 60)

    def test_env_var_overrides_default(self):
        with patch.dict(os.environ, {"WHOOP_TELEGRAM_WAIT_REVAL_MINUTES": "5"}, clear=False):
            self.assertEqual(self.pipeline._telegram_wait_revalidation_seconds(), 5 * 60)

    def test_env_zero_disables_periodic_check(self):
        with patch.dict(os.environ, {"WHOOP_TELEGRAM_WAIT_REVAL_MINUTES": "0"}, clear=False):
            self.assertEqual(self.pipeline._telegram_wait_revalidation_seconds(), 0)

    def test_global_revalidation_disabled_zeroes_interval(self):
        with patch.dict(
            os.environ,
            {"WHOOP_REVALIDATE_BEFORE_PUBLISH": "false", "WHOOP_TELEGRAM_WAIT_REVAL_MINUTES": "10"},
            clear=False,
        ):
            self.assertEqual(self.pipeline._telegram_wait_revalidation_seconds(), 0)

    def test_invalid_env_falls_back_to_default(self):
        with patch.dict(os.environ, {"WHOOP_TELEGRAM_WAIT_REVAL_MINUTES": "not-a-number"}, clear=False):
            self.assertEqual(self.pipeline._telegram_wait_revalidation_seconds(), 15 * 60)


class TelegramWaitRevalidationBehaviorTests(unittest.TestCase):
    """Validates that _revalidate_whoop_checkpoint can be invoked safely with
    checkpoint='during_telegram_wait' and produces the same fallback-eligible
    exception shape as the other checkpoints."""

    def setUp(self):
        self.pipeline = WHOOPPipeline.__new__(WHOOPPipeline)

    def _zone_change_snapshots(self):
        original = {
            "provenance_version": 1,
            "sleep_id": "sleep-A",
            "recovery_cycle_id": 100,
            "recovery_sleep_id": "sleep-A",
            "strain_cycle_id": 99,
            "public_values": {"strain": 11.22, "recovery_pct": 76.0, "sleep_score_pct": 82.0, "sleep_hours": 7.7},
            "zones": {"recovery_zone": "HIGH", "energy_zone": "MEDIUM", "sleep_score_zone": "MID-DEPTH", "moon_count": 3},
        }
        fresh = {
            **original,
            "public_values": {"strain": 11.22, "recovery_pct": 70.0, "sleep_score_pct": 82.0, "sleep_hours": 7.7},
            "zones": {"recovery_zone": "MID", "energy_zone": "MEDIUM", "sleep_score_zone": "MID-DEPTH", "moon_count": 3},
        }
        return original, fresh

    def test_during_telegram_wait_zone_change_raises_fallback_eligible(self):
        import tempfile, json
        from pipeline import PipelineStageError

        with tempfile.TemporaryDirectory() as tmpdir:
            self.pipeline.output_dir = Path(tmpdir)
            self.pipeline.post_to_instagram = True
            self.pipeline.run_date = "2026-05-26"
            self.pipeline.deadline_dt = datetime(2026, 5, 26, 14, 0, 0)
            self.pipeline._now = lambda: datetime(2026, 5, 26, 11, 15, 0)

            original, fresh = self._zone_change_snapshots()
            (Path(tmpdir) / "whoop_snapshot.json").write_text(json.dumps(original), encoding="utf-8")
            self.pipeline._fetch_fresh_whoop_snapshot = lambda: fresh

            daily_data = {"strain": 11.22, "recovery_pct": 76.0, "sleep_score_pct": 82.0, "sleep_hours": 7.7}

            with patch.dict(os.environ, {"PIPELINE_TERMINAL_RESCUE_RUN": "false"}, clear=False):
                with self.assertRaises(PipelineStageError) as ctx:
                    self.pipeline._revalidate_whoop_checkpoint(daily_data, checkpoint="during_telegram_wait")

            log = json.loads((Path(tmpdir) / "whoop_revalidation.json").read_text(encoding="utf-8"))

        self.assertTrue(ctx.exception.fallback_eligible)
        self.assertTrue(ctx.exception.emergency_fallback_allowed)
        self.assertEqual(ctx.exception.failure_classification, "lookup_not_ready")
        self.assertEqual(log["checks"][-1]["checkpoint"], "during_telegram_wait")
        self.assertEqual(log["checks"][-1]["decision"], "retry")
        self.assertTrue(any(m["field"] == "recovery_zone" for m in log["checks"][-1]["mismatches"]))

    def test_during_telegram_wait_same_zones_passes(self):
        import tempfile, json

        with tempfile.TemporaryDirectory() as tmpdir:
            self.pipeline.output_dir = Path(tmpdir)
            self.pipeline.post_to_instagram = True
            self.pipeline.run_date = "2026-05-26"
            self.pipeline.deadline_dt = datetime(2026, 5, 26, 14, 0, 0)
            self.pipeline._now = lambda: datetime(2026, 5, 26, 11, 15, 0)

            # Exact 2026-05-26 scenario: 81->82, 7.5->7.7, same zones.
            original = {
                "provenance_version": 1,
                "sleep_id": "sleep-A",
                "recovery_cycle_id": 100,
                "recovery_sleep_id": "sleep-A",
                "strain_cycle_id": 99,
                "public_values": {"strain": 11.22, "recovery_pct": 88.0, "sleep_score_pct": 81.0, "sleep_hours": 7.5},
                "zones": {"recovery_zone": "HIGH", "energy_zone": "MEDIUM", "sleep_score_zone": "MID-DEPTH", "moon_count": 3},
            }
            fresh = {
                **original,
                "public_values": {"strain": 11.22, "recovery_pct": 88.0, "sleep_score_pct": 82.0, "sleep_hours": 7.7},
                "zones": {"recovery_zone": "HIGH", "energy_zone": "MEDIUM", "sleep_score_zone": "MID-DEPTH", "moon_count": 3},
            }
            (Path(tmpdir) / "whoop_snapshot.json").write_text(json.dumps(original), encoding="utf-8")
            self.pipeline._fetch_fresh_whoop_snapshot = lambda: fresh

            daily_data = {"strain": 11.22, "recovery_pct": 88.0, "sleep_score_pct": 81.0, "sleep_hours": 7.5}

            with patch.dict(os.environ, {"PIPELINE_TERMINAL_RESCUE_RUN": "false"}, clear=False):
                with patch("builtins.print"):
                    self.pipeline._revalidate_whoop_checkpoint(daily_data, checkpoint="during_telegram_wait")

            log = json.loads((Path(tmpdir) / "whoop_revalidation.json").read_text(encoding="utf-8"))

        self.assertEqual(log["checks"][-1]["decision"], "pass")
        self.assertEqual(log["checks"][-1]["mismatches"], [])
        drift_fields = {d["field"] for d in log["checks"][-1]["drift"]}
        self.assertIn("sleep_score_pct", drift_fields)
        self.assertIn("sleep_hours", drift_fields)


if __name__ == "__main__":
    unittest.main()
