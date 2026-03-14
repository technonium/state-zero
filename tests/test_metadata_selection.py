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
from prompts import PromptOrchestrator
from title_utils import clean_title, normalize_title


class MetadataSelectionTests(unittest.TestCase):
    def _seed_title_history(self, db: CardDatabase, rows: list[tuple[str, str, str]]):
        for run_date, title, instagram_post_id in rows:
            db.insert_card(
                {
                    "date": run_date,
                    "title": title,
                    "scene_description": "scene",
                    "environment": "Frozen/Ice — quiet reason",
                    "environment_name": "Frozen/Ice",
                    "environment_reason": "quiet reason",
                    "creature": "Phoenix — rebirth",
                    "energy_zone": "LOW",
                    "image_path": "/tmp/image.png",
                    "video_path": "/tmp/video.mp4",
                    "instagram_post_id": instagram_post_id,
                }
            )

    def _daily_data(self, run_date: str = "2026-03-14"):
        return {
            "date": run_date,
            "depth_level": "Quiet",
            "behavior_matrix": {
                "body_keywords": ["steady", "contained"],
                "art_keywords": ["ashen", "open"],
                "one_liner": "The field held the charge without breaking.",
            },
        }

    def test_clean_title_normalizes_wrapped_noise(self):
        self.assertEqual(clean_title('**"ASH MERIDIAN"**.'), "ASH MERIDIAN")
        self.assertEqual(normalize_title('"Void Weight"'), "void weight")

    def test_get_recent_titles_returns_real_posts_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                db = CardDatabase()
                self._seed_title_history(
                    db,
                    [
                        ("2026-03-13", "VOID WEIGHT", "ig_real_001"),
                        ("2026-03-12", "HOLLOW DRIFT", "ig_real_002"),
                        ("2026-03-11", "VOID WEIGHT", "ig_real_003"),
                        ("2026-03-10", "MOCK TITLE", "mock_ig_12345"),
                        ("2026-03-09", "EMPTY TITLE", ""),
                    ],
                )

                titles = db.get_recent_titles("2026-03-14", limit=10)

        self.assertEqual(titles, ["VOID WEIGHT", "HOLLOW DRIFT", "VOID WEIGHT"])

    def test_extract_metadata_renders_none_when_history_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: json.dumps(
                    {
                        "title": "ASH MERIDIAN",
                        "scene_description": "The field opened past the last edge.",
                        "date_display": "MARCH 14 2026",
                    }
                )

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                prompt_text = (orchestrator.output_dir / "last_prompt_metadata.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "ASH MERIDIAN")
        self.assertIn("## Recent Titles (Do Not Repeat)", prompt_text)
        self.assertIn("None — no restriction.", prompt_text)
        self.assertEqual(debug_payload["final_selection_source"], "llm_valid")
        self.assertEqual(debug_payload["recent_titles_used"], [])

    def test_extract_metadata_retries_on_duplicate_title_and_accepts_unique_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                db = CardDatabase()
                self._seed_title_history(
                    db,
                    [
                        ("2026-03-13", "VOID WEIGHT", "ig_real_001"),
                        ("2026-03-12", "VOID WEIGHT", "ig_real_002"),
                        ("2026-03-11", "HOLLOW DRIFT", "ig_real_003"),
                    ],
                )
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(
                    [
                        json.dumps(
                            {
                                "title": "VOID WEIGHT",
                                "scene_description": "The compression settled in plain sight.",
                                "date_display": "MARCH 14 2026",
                            }
                        ),
                        json.dumps(
                            {
                                "title": "ASH MERIDIAN",
                                "scene_description": "The field opened past the last edge.",
                                "date_display": "MARCH 14 2026",
                            }
                        ),
                    ]
                )
                prompts_seen = []

                def fake_call(prompt: str) -> str:
                    prompts_seen.append(prompt)
                    return next(responses)

                orchestrator.call_llm = fake_call
                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                retry_prompt = (orchestrator.output_dir / "last_prompt_metadata_retry.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "ASH MERIDIAN")
        self.assertEqual(len(prompts_seen), 2)
        self.assertIn("Recent banned titles: VOID WEIGHT, HOLLOW DRIFT", retry_prompt)
        self.assertEqual(debug_payload["final_selection_source"], "corrective_retry_valid")
        self.assertEqual(debug_payload["final_title"], "ASH MERIDIAN")
        self.assertEqual(debug_payload["recent_titles_used"], ["VOID WEIGHT", "HOLLOW DRIFT"])

    def test_extract_metadata_keeps_second_metadata_on_duplicate_retry_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                db = CardDatabase()
                self._seed_title_history(db, [("2026-03-13", "VOID WEIGHT", "ig_real_001")])
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(
                    [
                        json.dumps(
                            {
                                "title": "VOID WEIGHT",
                                "scene_description": "The compression settled in plain sight.",
                                "date_display": "MARCH 14 2026",
                            }
                        ),
                        json.dumps(
                            {
                                "title": "VOID WEIGHT",
                                "scene_description": "Nothing moved and the seal held.",
                                "date_display": "MARCH 14 2026",
                            }
                        ),
                    ]
                )
                orchestrator.call_llm = lambda prompt: next(responses)

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "VOID WEIGHT")
        self.assertEqual(metadata["scene_description"], "Nothing moved and the seal held.")
        self.assertEqual(debug_payload["final_selection_source"], "repeat_after_retry_warning")

    def test_extract_metadata_uses_json_fallback_when_metadata_never_parses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(["not-json", "still not json"])
                orchestrator.call_llm = lambda prompt: next(responses)

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                    image_json={"core_concept": "A frozen field holding its shape under pressure."},
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "Daily State Card")
        self.assertEqual(debug_payload["final_selection_source"], "json_fallback")

    def test_extract_metadata_handles_db_lookup_failure_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: json.dumps(
                    {
                        "title": "ASH MERIDIAN",
                        "scene_description": "The field opened past the last edge.",
                        "date_display": "MARCH 14 2026",
                    }
                )
                with patch.object(CardDatabase, "get_recent_titles", side_effect=RuntimeError("db unavailable")):
                    metadata = orchestrator.extract_metadata(
                        self._daily_data(),
                        "MARCH 14 2026",
                        environment="Frozen/Ice — quiet reason",
                    )
                prompt_text = (orchestrator.output_dir / "last_prompt_metadata.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "ASH MERIDIAN")
        self.assertIn("None — no restriction.", prompt_text)
        self.assertEqual(debug_payload["final_selection_source"], "history_lookup_failed_no_guard")


if __name__ == "__main__":
    unittest.main()
