import json
import os
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

    def test_recent_environment_names_exclude_mock_posts(self):
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
                        "date": "2026-03-08",
                        "title": "Real Environment Title",
                        "scene_description": "scene",
                        "environment": "Frozen/Ice — stored reason",
                        "environment_name": "Frozen/Ice",
                        "environment_reason": "stored reason",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "instagram_post_id": "ig_real_123",
                    }
                )
                db.insert_card(
                    {
                        "date": "2026-03-07",
                        "title": "Mock Environment Title",
                        "scene_description": "scene",
                        "environment": "Crystal Caves — stored reason",
                        "environment_name": "Crystal Caves",
                        "environment_reason": "stored reason",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "instagram_post_id": "mock_ig_12345",
                    }
                )

                names = db.get_recent_environment_names("LOW", "2026-03-09", limit=5)

        self.assertEqual(names, ["Frozen/Ice"])

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
