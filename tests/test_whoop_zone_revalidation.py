import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import lookups
from pipeline import WHOOPPipeline


def _snapshot(**overrides) -> dict:
    """Build a baseline snapshot.

    Defaults: recovery 88 (HIGH), strain 11.22 (MEDIUM energy),
    sleep_score 82 (MID-DEPTH), sleep_hours 7.7 (Moon 3).
    """
    public_values = {
        "strain": 11.22,
        "recovery_pct": 88.0,
        "sleep_score_pct": 82.0,
        "sleep_hours": 7.7,
    }
    public_values.update(overrides.pop("public_values_override", {}))
    snap = {
        "provenance_version": 1,
        "sleep_id": "sleep-A",
        "recovery_cycle_id": 100,
        "recovery_sleep_id": "sleep-A",
        "strain_cycle_id": 99,
        "public_values": public_values,
        "zones": lookups.derive_whoop_zones(public_values),
    }
    snap.update(overrides)
    return snap


class ZoneAwareRevalidationTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = WHOOPPipeline.__new__(WHOOPPipeline)

    def test_intra_zone_drift_passes_with_drift_logged(self):
        """2026-05-26 scenario: 81->82, 7.5->7.7 — same zones, same IDs."""
        original = _snapshot(public_values_override={"sleep_score_pct": 81.0, "sleep_hours": 7.5})
        fresh = _snapshot()  # 82, 7.7 — same zones (MID-DEPTH, Moon 3)
        mismatches, drift = self.pipeline._compare_whoop_snapshots(original, fresh)
        self.assertEqual(mismatches, [])
        drift_fields = {d["field"] for d in drift}
        self.assertIn("sleep_score_pct", drift_fields)
        self.assertIn("sleep_hours", drift_fields)

    def test_recovery_zone_boundary_cross_is_mismatch(self):
        """Recovery 76 (HIGH) -> 75 (MID) — boundary cross."""
        original = _snapshot(public_values_override={"recovery_pct": 76.0})
        fresh = _snapshot(public_values_override={"recovery_pct": 75.0})
        mismatches, _drift = self.pipeline._compare_whoop_snapshots(original, fresh)
        zone_mismatches = [m for m in mismatches if m["field"] == "recovery_zone"]
        self.assertEqual(len(zone_mismatches), 1)
        self.assertEqual(zone_mismatches[0]["type"], "zone_change")
        self.assertEqual(zone_mismatches[0]["original"], "HIGH")
        self.assertEqual(zone_mismatches[0]["fresh"], "MID")

    def test_sleep_score_zone_cross_is_mismatch(self):
        """Sleep score 84 (SURFACE) -> 83 (MID-DEPTH)."""
        original = _snapshot(public_values_override={"sleep_score_pct": 84.0})
        fresh = _snapshot(public_values_override={"sleep_score_pct": 83.0})
        mismatches, _drift = self.pipeline._compare_whoop_snapshots(original, fresh)
        zone_mismatches = [m for m in mismatches if m["field"] == "sleep_score_zone"]
        self.assertEqual(len(zone_mismatches), 1)
        self.assertEqual(zone_mismatches[0]["original"], "SURFACE")
        self.assertEqual(zone_mismatches[0]["fresh"], "MID-DEPTH")

    def test_moon_count_cross_is_mismatch(self):
        """Sleep hours 7.5 (Moon 3) -> 7.4 (Moon 2)."""
        original = _snapshot(public_values_override={"sleep_hours": 7.5})
        fresh = _snapshot(public_values_override={"sleep_hours": 7.4})
        mismatches, _drift = self.pipeline._compare_whoop_snapshots(original, fresh)
        moon_mismatches = [m for m in mismatches if m["field"] == "moon_count"]
        self.assertEqual(len(moon_mismatches), 1)
        self.assertEqual(moon_mismatches[0]["original"], 3)
        self.assertEqual(moon_mismatches[0]["fresh"], 2)

    def test_energy_zone_cross_is_mismatch(self):
        """Strain 14 (HIGH) -> 13.9 (MEDIUM)."""
        original = _snapshot(public_values_override={"strain": 14.0})
        fresh = _snapshot(public_values_override={"strain": 13.9})
        mismatches, _drift = self.pipeline._compare_whoop_snapshots(original, fresh)
        energy_mismatches = [m for m in mismatches if m["field"] == "energy_zone"]
        self.assertEqual(len(energy_mismatches), 1)
        self.assertEqual(energy_mismatches[0]["original"], "HIGH")
        self.assertEqual(energy_mismatches[0]["fresh"], "MEDIUM")

    def test_sleep_id_change_is_mismatch(self):
        original = _snapshot(sleep_id="sleep-A")
        fresh = _snapshot(sleep_id="sleep-B")
        mismatches, _drift = self.pipeline._compare_whoop_snapshots(original, fresh)
        id_mismatches = [m for m in mismatches if m["field"] == "sleep_id"]
        self.assertEqual(len(id_mismatches), 1)
        self.assertEqual(id_mismatches[0]["type"], "id_change")

    def test_cycle_id_change_is_mismatch(self):
        original = _snapshot(strain_cycle_id=99)
        fresh = _snapshot(strain_cycle_id=100)
        mismatches, _drift = self.pipeline._compare_whoop_snapshots(original, fresh)
        self.assertTrue(any(m["field"] == "strain_cycle_id" for m in mismatches))

    def test_backward_compat_snapshot_without_zones_block(self):
        """Older snapshots that pre-date the `zones` field — zones recomputed from public_values."""
        original_no_zones = _snapshot()
        del original_no_zones["zones"]
        fresh_no_zones = _snapshot(public_values_override={"sleep_score_pct": 81.0})
        del fresh_no_zones["zones"]
        mismatches, drift = self.pipeline._compare_whoop_snapshots(original_no_zones, fresh_no_zones)
        # Same zones (both MID-DEPTH), only drift.
        self.assertEqual(mismatches, [])
        self.assertTrue(any(d["field"] == "sleep_score_pct" for d in drift))

    def test_drift_only_logs_when_delta_exceeds_tolerance(self):
        original = _snapshot(public_values_override={"sleep_score_pct": 82.0})
        # Drift of exactly 0.5 == tolerance for sleep_score_pct: NOT logged (strictly greater).
        fresh = _snapshot(public_values_override={"sleep_score_pct": 82.4})
        mismatches, drift = self.pipeline._compare_whoop_snapshots(original, fresh)
        self.assertEqual(mismatches, [])
        sleep_drift = [d for d in drift if d["field"] == "sleep_score_pct"]
        self.assertEqual(sleep_drift, [])  # within tolerance, no drift entry

    def test_missing_zone_field_produces_zone_missing_mismatch(self):
        """A snapshot whose public_values can't yield zones (e.g., None values) fails closed."""
        original = _snapshot()
        broken = _snapshot()
        broken["zones"] = {}  # explicitly empty
        broken["public_values"] = {}  # also empty -> derive returns {} -> zone fields are None
        mismatches, _drift = self.pipeline._compare_whoop_snapshots(original, broken)
        types_seen = {m["type"] for m in mismatches}
        self.assertIn("zone_missing", types_seen)


class WhoopSnapshotZonesBuilderTests(unittest.TestCase):
    def test_build_whoop_snapshot_includes_zones(self):
        from datetime import date
        public_values = {
            "strain": 11.22,
            "recovery_pct": 88.0,
            "sleep_score_pct": 82.0,
            "sleep_hours": 7.7,
        }
        snapshot = lookups.build_whoop_snapshot(
            target_date=date(2026, 5, 26),
            sleep_data={"id": "s1"},
            recovery_data={"cycle_id": 1, "sleep_id": "s1"},
            strain_cycle={"id": 2},
            public_values=public_values,
        )
        self.assertEqual(snapshot["zones"]["recovery_zone"], "HIGH")
        self.assertEqual(snapshot["zones"]["energy_zone"], "MEDIUM")
        self.assertEqual(snapshot["zones"]["sleep_score_zone"], "MID-DEPTH")
        self.assertEqual(snapshot["zones"]["moon_count"], 3)

    def test_derive_whoop_zones_handles_bad_public_values(self):
        self.assertEqual(lookups.derive_whoop_zones({}), {})
        self.assertEqual(lookups.derive_whoop_zones({"strain": "nope"}), {})


if __name__ == "__main__":
    unittest.main()
