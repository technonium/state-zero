import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from database_manager import CardDatabase
from environment_utils import extract_valid_environment_name, select_least_recent_candidate
from prompts import PromptOrchestrator


class EnvironmentSelectionTests(unittest.TestCase):
    def _build_orchestrator(self, tmpdir: str) -> PromptOrchestrator:
        with patch.dict(
            os.environ,
            {
                "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                "PIPELINE_DATE": "2026-03-09",
            },
            clear=False,
        ):
            return PromptOrchestrator(llm_api_key="mock")

    def _seed_low_history(self, db: CardDatabase):
        historical = [
            ("2026-03-08", "Frozen/Ice"),
            ("2026-03-07", "Crystal Caves"),
            ("2026-03-06", "Stone Monuments"),
            ("2026-03-05", "Mist/Fog Realms"),
            ("2026-03-04", "Void/Space (Low)"),
        ]
        for run_date, environment_name in historical:
            db.insert_card(
                {
                    "date": run_date,
                    "title": f"{environment_name} Title",
                    "scene_description": "scene",
                    "environment": f"{environment_name} — stored reason",
                    "environment_name": environment_name,
                    "environment_reason": "stored reason",
                    "energy_zone": "LOW",
                    "image_path": "/tmp/image.png",
                    "video_path": "/tmp/video.mp4",
                    "instagram_post_id": f"ig_{run_date}",
                }
            )

    def test_generate_environment_persists_history_and_counts_for_recency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: "Frozen/Ice — ancient carved silence"

                result = orchestrator.generate_environment(
                    {"date": "2026-03-09", "energy_zone": "LOW"},
                    "quiet interpretation",
                    persist_environment_history=True,
                )

                db = CardDatabase()
                recent_names = db.get_recent_environment_names("LOW", "2026-03-10", limit=5)
                conn = sqlite3.connect(db.db_path)
                history_row = conn.execute(
                    """
                    SELECT environment_name, selection_stage
                    FROM environment_history
                    WHERE date = ?
                    """,
                    ("2026-03-09",),
                ).fetchone()
                conn.close()
                debug_payload = json.loads(
                    (orchestrator.output_dir / "environment_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, "Frozen/Ice — ancient carved silence")
        self.assertEqual(recent_names, ["Frozen/Ice"])
        self.assertEqual(history_row, ("Frozen/Ice", "environment_selected"))
        self.assertEqual(debug_payload["history_persist_status"], "ok")

    def test_generate_environment_skips_history_persist_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: "Frozen/Ice — ancient carved silence"

                result = orchestrator.generate_environment(
                    {"date": "2026-03-09", "energy_zone": "LOW"},
                    "quiet interpretation",
                )

                db = CardDatabase()
                recent_names = db.get_recent_environment_names("LOW", "2026-03-10", limit=5)
                debug_payload = json.loads(
                    (orchestrator.output_dir / "environment_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, "Frozen/Ice — ancient carved silence")
        self.assertEqual(recent_names, [])
        self.assertEqual(debug_payload["history_persist_status"], "skipped_non_persistent_run")
        self.assertIsNone(debug_payload["history_persist_error"])

    def test_insert_card_upgrades_environment_selected_to_cards_archive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: "Frozen/Ice — ancient carved silence"
                orchestrator.generate_environment(
                    {"date": "2026-03-09", "energy_zone": "LOW"},
                    "quiet interpretation",
                    persist_environment_history=True,
                )

                db = CardDatabase()
                db.insert_card(
                    {
                        "date": "2026-03-09",
                        "title": "Title",
                        "scene_description": "scene",
                        "environment": "Frozen/Ice — ancient carved silence",
                        "environment_name": "Frozen/Ice",
                        "environment_reason": "ancient carved silence",
                        "creature": "Moth — quiet drift",
                        "blend_option": "Option A",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "image_prompt_json": "{}",
                        "instagram_post_id": "ig_2026-03-09",
                    }
                )

                # Reopening the database reruns migration/backfill paths.
                reopened_db = CardDatabase()
                conn = sqlite3.connect(reopened_db.db_path)
                history_row = conn.execute(
                    """
                    SELECT selection_stage
                    FROM environment_history
                    WHERE date = ?
                    """,
                    ("2026-03-09",),
                ).fetchone()
                conn.close()

        self.assertEqual(history_row, ("cards_archive",))

    def test_complete_archive_requires_card_and_archived_environment_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                db = CardDatabase()
                db.upsert_environment_history(
                    run_date="2026-03-09",
                    energy_zone="LOW",
                    environment_name="Frozen/Ice",
                    environment_text="Frozen/Ice — selected only",
                    selection_stage="environment_selected",
                )

                self.assertFalse(db.has_archived_environment_history_for_date("2026-03-09"))
                self.assertFalse(db.has_complete_archive_for_date("2026-03-09"))

                db.insert_card(
                    {
                        "date": "2026-03-09",
                        "title": "Title",
                        "scene_description": "scene",
                        "environment": "Frozen/Ice — archived reason",
                        "environment_name": "Frozen/Ice",
                        "environment_reason": "archived reason",
                        "creature": "Moth — quiet drift",
                        "blend_option": "Option A",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "image_prompt_json": "{}",
                        "instagram_post_id": "ig_2026-03-09",
                    }
                )

                self.assertTrue(db.has_archived_environment_history_for_date("2026-03-09"))
                self.assertTrue(db.has_complete_archive_for_date("2026-03-09"))

    def test_environment_history_backfills_missing_real_rows_without_restoring_mock_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                db = CardDatabase()
                db.insert_card(
                    {
                        "date": "2026-03-06",
                        "title": "Real One",
                        "scene_description": "scene",
                        "environment": "Frozen/Ice — stored reason",
                        "environment_name": "Frozen/Ice",
                        "environment_reason": "stored reason",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "instagram_post_id": "ig_real_1",
                    }
                )
                db.insert_card(
                    {
                        "date": "2026-03-07",
                        "title": "Real Two",
                        "scene_description": "scene",
                        "environment": "Crystal Caves — stored reason",
                        "environment_name": "Crystal Caves",
                        "environment_reason": "stored reason",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "instagram_post_id": "ig_real_2",
                    }
                )
                db.insert_card(
                    {
                        "date": "2026-03-08",
                        "title": "Mock Run",
                        "scene_description": "scene",
                        "environment": "Stone Monuments — stored reason",
                        "environment_name": "Stone Monuments",
                        "environment_reason": "stored reason",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "instagram_post_id": "mock_ig_12345",
                    }
                )

                conn = sqlite3.connect(db.db_path)
                conn.execute("DELETE FROM environment_history WHERE date IN (?, ?)", ("2026-03-07", "2026-03-08"))
                conn.commit()
                conn.close()

                reopened_db = CardDatabase()
                conn = sqlite3.connect(reopened_db.db_path)
                history_rows = conn.execute(
                    """
                    SELECT date, environment_name, selection_stage
                    FROM environment_history
                    ORDER BY date ASC
                    """
                ).fetchall()
                conn.close()

        self.assertEqual(
            history_rows,
            [
                ("2026-03-06", "Frozen/Ice", "cards_archive"),
                ("2026-03-07", "Crystal Caves", "cards_backfill"),
            ],
        )

    def test_glacial_valley_is_wired_in_templates(self):
        json_builder = (PROJECT_ROOT / "src/prompts/json_builder.md").read_text(encoding="utf-8")
        video_prompt = (PROJECT_ROOT / "src/prompts/video.md").read_text(encoding="utf-8")

        self.assertIn("**Glacial Valley:** Polished bedrock, glacial moraine", json_builder)
        self.assertIn(
            "| **Glacial Valley** | near absolute stillness; a thin mist drifts slowly across the valley floor; light shifts faintly across polished stone |",
            video_prompt,
        )

    def test_generate_environment_filters_recent_same_zone_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                db = CardDatabase()
                self._seed_low_history(db)
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: "Glacial Valley — ancient carved silence"

                result = orchestrator.generate_environment(
                    {"date": "2026-03-09", "energy_zone": "LOW"},
                    "quiet interpretation",
                )

                prompt_text = (orchestrator.output_dir / "last_prompt_environment.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "environment_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, "Glacial Valley — ancient carved silence")
        self.assertIn("- Glacial Valley — Polished bedrock, glacial moraine, still cold tarns, smooth U-shaped rock walls, ancient carved silence", prompt_text)
        self.assertNotIn("- Frozen/Ice — Transparent ice, frost, frozen atmospheric effects", prompt_text)
        self.assertEqual(debug_payload["candidate_names"], ["Glacial Valley"])
        self.assertEqual(debug_payload["final_selection_source"], "llm_valid")

    def test_generate_environment_repairs_noisy_valid_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                db = CardDatabase()
                self._seed_low_history(db)
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: 'I choose "Glacial Valley" because it best matches the mood.'

                result = orchestrator.generate_environment(
                    {"date": "2026-03-09", "energy_zone": "LOW"},
                    "quiet interpretation",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "environment_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, "Glacial Valley")
        self.assertEqual(debug_payload["final_selection_source"], "repaired")
        self.assertEqual(debug_payload["final_name"], "Glacial Valley")

    def test_recent_environment_names_include_selected_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                db = CardDatabase()
                db.upsert_environment_history(
                    run_date="2026-03-08",
                    energy_zone="LOW",
                    environment_name="Frozen/Ice",
                    environment_text="Frozen/Ice — stored reason",
                    selection_stage="cards_archive",
                )
                db.upsert_environment_history(
                    run_date="2026-03-07",
                    energy_zone="LOW",
                    environment_name="Crystal Caves",
                    environment_text="Crystal Caves — stored reason",
                    selection_stage="cards_backfill",
                )
                db.upsert_environment_history(
                    run_date="2026-03-06",
                    energy_zone="LOW",
                    environment_name="Stone Monuments",
                    environment_text="Stone Monuments — selected but not archived",
                    selection_stage="environment_selected",
                )

                names = db.get_recent_environment_names("LOW", "2026-03-09", limit=5)

        self.assertEqual(names, ["Frozen/Ice", "Crystal Caves", "Stone Monuments"])

    def test_repair_selected_environment_history_from_output_uses_authoritative_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                output_dir = Path(tmpdir) / "runtime" / "output" / "2026-03-09"
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "environment_selected.txt").write_text(
                    "Frozen/Ice — ancient carved silence",
                    encoding="utf-8",
                )
                (output_dir / "daily_data.json").write_text(
                    json.dumps({"date": "2026-03-09", "energy_zone": "LOW"}),
                    encoding="utf-8",
                )

                db = CardDatabase()
                repaired = db.repair_selected_environment_history_from_output(["2026-03-09"])

                conn = sqlite3.connect(db.db_path)
                row = conn.execute(
                    """
                    SELECT environment_name, selection_stage
                    FROM environment_history
                    WHERE date = ?
                    """,
                    ("2026-03-09",),
                ).fetchone()
                conn.close()

        self.assertEqual(repaired, 1)
        self.assertEqual(row, ("Frozen/Ice", "environment_selected"))

    def test_repair_selected_environment_history_from_output_requires_explicit_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                db = CardDatabase()

                with self.assertRaises(ValueError):
                    db.repair_selected_environment_history_from_output([])

    def test_extract_valid_environment_name_prefers_explicit_choice_cue(self):
        repaired_name, status = extract_valid_environment_name(
            'I choose "Glacial Valley" because it fits better.',
            ["Stone Monuments", "Glacial Valley"],
        )

        self.assertEqual(repaired_name, "Glacial Valley")
        self.assertTrue(status.startswith("cue_"))

    def test_extract_valid_environment_name_rejects_ambiguous_multi_match_line(self):
        repaired_name, status = extract_valid_environment_name(
            "Stone Monuments would be too rigid; Glacial Valley fits better.",
            ["Stone Monuments", "Glacial Valley"],
        )

        self.assertIsNone(repaired_name)
        self.assertEqual(status, "ambiguous_multi_match")

    def test_select_least_recent_candidate_treats_first_occurrence_as_most_recent(self):
        selected = select_least_recent_candidate(
            ["Frozen/Ice", "Stone Monuments"],
            ["Frozen/Ice", "Stone Monuments", "Frozen/Ice"],
        )

        self.assertEqual(selected, "Stone Monuments")

    def test_select_least_recent_candidate_prefers_missing_history(self):
        selected = select_least_recent_candidate(
            ["Frozen/Ice", "Crystal Caves", "Stone Monuments"],
            ["Frozen/Ice", "Stone Monuments", "Frozen/Ice"],
        )

        self.assertEqual(selected, "Crystal Caves")

    def test_generate_environment_falls_back_deterministically_on_invalid_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                db = CardDatabase()
                self._seed_low_history(db)
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: "Invented Realm — nonsense"

                result = orchestrator.generate_environment(
                    {"date": "2026-03-09", "energy_zone": "LOW"},
                    "quiet interpretation",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "environment_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, "Glacial Valley — Selected deterministically after invalid environment output.")
        self.assertEqual(debug_payload["final_selection_source"], "deterministic_fallback")
        self.assertEqual(debug_payload["final_name"], "Glacial Valley")

    def test_generate_environment_ambiguous_multi_match_falls_back_deterministically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-09",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = (
                    lambda prompt: "Stone Monuments would be too rigid; Glacial Valley fits better."
                )

                result = orchestrator.generate_environment(
                    {"date": "2026-03-09", "energy_zone": "LOW"},
                    "quiet interpretation",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "environment_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, "Frozen/Ice — Selected deterministically after invalid environment output.")
        self.assertEqual(debug_payload["repair_status"], "ambiguous_multi_match")
        self.assertEqual(debug_payload["final_selection_source"], "deterministic_fallback")


if __name__ == "__main__":
    unittest.main()
