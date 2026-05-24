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
            "depth_level": "SURFACE",
            "behavior_matrix": {
                "body_keywords": ["steady", "contained"],
                "art_keywords": ["ashen", "open"],
                "one_liner": "The field held the charge without breaking.",
            },
        }

    def _metadata_responder(self, title_outputs: list[str], scene_outputs: list[str]):
        title_iter = iter(title_outputs)
        scene_iter = iter(scene_outputs)

        def fake_call(prompt: str) -> str:
            if "# Card Title Builder" in prompt:
                return next(title_iter)
            if "# Card Scene Description Builder" in prompt:
                return next(scene_iter)
            raise AssertionError(f"Unexpected prompt:\n{prompt}")

        return fake_call

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

    def test_extract_metadata_renders_empty_history_in_title_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = self._metadata_responder(
                    ["ASH MERIDIAN\nCLEAR HORIZON\nGLASS SHELF\nSTONE REACH\nDEEP VAULT"],
                    ["The field opened past the last edge. Everything reached and nothing pushed back."],
                )

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                title_prompt = (orchestrator.output_dir / "last_prompt_title.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "ASH MERIDIAN")
        self.assertIn("## Recent Exact Titles (Do Not Repeat)", title_prompt)
        self.assertIn("None — no restriction.", title_prompt)
        self.assertEqual(debug_payload["title_selection_source"], "first_batch_valid")
        self.assertEqual(debug_payload["recent_titles_used"], [])
        self.assertEqual(debug_payload["recent_structural_keys_used"], [])

    def test_extract_metadata_retries_when_first_batch_has_only_exact_or_structural_repeats(self):
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
                        ("2026-03-13", "ASH MERIDIAN", "ig_real_001"),
                        ("2026-03-12", "CLEAR REACH", "ig_real_002"),
                        ("2026-03-11", "HOLLOW DRIFT", "ig_real_003"),
                    ],
                )
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = self._metadata_responder(
                    [
                        "ASH MERIDIAN\nMERIDIAN\nSTONE REACH\nCLEAR REACH\nEMBER REACH",
                        "BASALT VISTA\nGLASS HARBOR\nSILENT BASIN\nFROST LINE\nCLEARING",
                    ],
                    ["The field opened past the last edge. Everything reached and nothing pushed back."],
                )

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                retry_prompt = (orchestrator.output_dir / "last_prompt_title_retry.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "BASALT VISTA")
        self.assertIn("Structural keys to avoid: MERIDIAN, REACH, DRIFT", retry_prompt)
        self.assertEqual(debug_payload["title_selection_source"], "retry_batch_valid")
        self.assertEqual(debug_payload["recent_structural_keys_used"], ["MERIDIAN", "REACH", "DRIFT"])

    def test_extract_metadata_soft_accepts_structural_collision_only_after_retry_exhausted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                db = CardDatabase()
                self._seed_title_history(db, [("2026-03-13", "CLEAR REACH", "ig_real_001")])
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = self._metadata_responder(
                    [
                        "OBSIDIAN REACH\nEMBER REACH\nSTONE REACH\nCLEAR REACH\nREACH",
                        "CLEAR REACH\nREACH\nASH REACH\nWIDE REACH\nLOW REACH",
                    ],
                    ["Nothing moved and the seal held. The quiet stayed exactly where it landed."],
                )

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "OBSIDIAN REACH")
        self.assertEqual(
            debug_payload["title_selection_source"],
            "soft_accept_family_collision_first_batch",
        )

    def test_extract_metadata_retries_scene_description_and_uses_safe_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = self._metadata_responder(
                    ["BASALT VISTA\nGLASS HARBOR\nSILENT BASIN\nFROST LINE\nCLEARING"],
                    ["Recovery stayed low.", "body held 95% strain at the floor."],
                )

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                    image_json={"core_concept": "A frozen field holding its shape under pressure."},
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertNotEqual(metadata["scene_description"], "A frozen field holding its shape under pressure.")
        self.assertEqual(debug_payload["scene_selection_source"], "fallback_safe_scene")
        self.assertFalse(orchestrator._validate_scene_description(metadata["scene_description"]))
        self.assertTrue(debug_payload["scene_debug"]["retry_triggered"])

    def test_extract_metadata_handles_db_lookup_failure_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-14"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = self._metadata_responder(
                    ["ASH MERIDIAN\nCLEAR HORIZON\nGLASS SHELF\nSTONE REACH\nDEEP VAULT"],
                    ["The field opened past the last edge. Everything reached and nothing pushed back."],
                )
                with patch.object(CardDatabase, "get_recent_titles", side_effect=RuntimeError("db unavailable")):
                    metadata = orchestrator.extract_metadata(
                        self._daily_data(),
                        "MARCH 14 2026",
                        environment="Frozen/Ice — quiet reason",
                    )
                combined_prompt = (orchestrator.output_dir / "last_prompt_metadata.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "ASH MERIDIAN")
        self.assertIn("## Title Prompt", combined_prompt)
        self.assertIn("## Scene Description Prompt", combined_prompt)
        self.assertEqual(debug_payload["title_selection_source"], "history_lookup_failed_no_guard")

    def test_extract_metadata_mock_mode_smoke_returns_non_empty_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-14",
                    "OPENROUTER_API_KEY": "",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                    image_json={"core_concept": "A frozen field holding its shape under pressure."},
                )

        self.assertTrue(metadata["title"])
        self.assertTrue(metadata["scene_description"])
        self.assertEqual(metadata["date_display"], "MARCH 14 2026")

    def test_extract_metadata_uses_only_last_ten_eligible_posted_titles_for_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-20"},
                clear=False,
            ):
                db = CardDatabase()
                self._seed_title_history(
                    db,
                    [
                        ("2026-03-21", "FUTURE SPIRE", "ig_real_future"),
                        ("2026-03-19", "ALPHA RIDGE", "ig_real_001"),
                        ("2026-03-18", "MOCK HORIZON", "mock_ig_12345"),
                        ("2026-03-17", "EMPTY FIELD", ""),
                        ("2026-03-16", "BRONZE BASIN", "ig_real_002"),
                        ("2026-03-15", "CINDER VAULT", "ig_real_003"),
                        ("2026-03-14", "DELTA REACH", "ig_real_004"),
                        ("2026-03-13", "EMBER LINE", "ig_real_005"),
                        ("2026-03-12", "FROST FLATS", "ig_real_006"),
                        ("2026-03-11", "GLASS SHELF", "ig_real_007"),
                        ("2026-03-10", "HALO FRONT", "ig_real_008"),
                        ("2026-03-09", "IVORY SPAN", "ig_real_009"),
                        ("2026-03-08", "JADE CHAMBER", "ig_real_010"),
                        ("2026-03-07", "KELP MONOLITH", "ig_real_011"),
                        ("2026-03-06", "LUNAR PLAIN", "ig_real_012"),
                    ],
                )
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = self._metadata_responder(
                    ["BASALT VISTA\nGLASS HARBOR\nSILENT BASIN\nFROST LINE\nCLEARING"],
                    ["The field opened past the last edge. Everything reached and nothing pushed back."],
                )

                orchestrator.extract_metadata(
                    self._daily_data(run_date="2026-03-20"),
                    "MARCH 20 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(
            debug_payload["recent_titles_used"],
            [
                "ALPHA RIDGE",
                "BRONZE BASIN",
                "CINDER VAULT",
                "DELTA REACH",
                "EMBER LINE",
                "FROST FLATS",
                "GLASS SHELF",
                "HALO FRONT",
                "IVORY SPAN",
                "JADE CHAMBER",
            ],
        )
        self.assertEqual(
            debug_payload["recent_structural_keys_used"],
            ["RIDGE", "BASIN", "VAULT", "REACH", "LINE", "FLATS", "SHELF", "FRONT", "SPAN", "CHAMBER"],
        )

    def test_extract_metadata_prefers_retry_valid_title_over_first_batch_soft_accept(self):
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
                        ("2026-03-13", "CLEAR REACH", "ig_real_001"),
                        ("2026-03-12", "SILENT BASIN", "ig_real_002"),
                        ("2026-03-11", "DARK LINE", "ig_real_003"),
                    ],
                )
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = self._metadata_responder(
                    [
                        "OBSIDIAN REACH\nEMBER BASIN\nFROST LINE\nWIDE REACH\nSALT BASIN",
                        "BASALT VISTA\nGLASS HARBOR\nSILENT BASIN\nFROST LINE\nCLEARING",
                    ],
                    ["The field opened past the last edge. Everything reached and nothing pushed back."],
                )

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(metadata["title"], "BASALT VISTA")
        self.assertEqual(debug_payload["title_selection_source"], "retry_batch_valid")
        first_assessments = debug_payload["title_debug"]["first_candidate_assessments"]
        self.assertTrue(all(item["is_soft_acceptable"] for item in first_assessments))
        self.assertTrue(
            all(item["soft_rejection_reasons"] == ["structural_recent_repeat"] for item in first_assessments)
        )

    def test_extract_metadata_falls_back_when_both_title_batches_are_hard_invalid(self):
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
                        ("2026-03-13", "ASH MERIDIAN", "ig_real_001"),
                        ("2026-03-12", "CLEAR REACH", "ig_real_002"),
                    ],
                )
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = self._metadata_responder(
                    [
                        "ASH MERIDIAN\nTOR\nTHREE WORD TITLE\nNO\nARC",
                        "CLEAR REACH\nICE\nANOTHER BAD TITLE\nX\nVOID",
                    ],
                    ["The field opened past the last edge. Everything reached and nothing pushed back."],
                )

                metadata = orchestrator.extract_metadata(
                    self._daily_data(),
                    "MARCH 14 2026",
                    environment="Frozen/Ice — quiet reason",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "metadata_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(debug_payload["title_selection_source"], "hard_failure_fallback")
        self.assertTrue(metadata["title"])
        self.assertTrue(metadata["scene_description"])
        self.assertEqual(metadata["date_display"], "MARCH 14 2026")
        self.assertEqual(debug_payload["final_title"], metadata["title"])
        self.assertTrue(debug_payload["title_debug"]["first_candidate_assessments"])
        self.assertTrue(debug_payload["title_debug"]["retry_candidate_assessments"])
        self.assertTrue(
            all(item["hard_rejection_reasons"] for item in debug_payload["title_debug"]["first_candidate_assessments"])
        )
        self.assertTrue(
            all(item["hard_rejection_reasons"] for item in debug_payload["title_debug"]["retry_candidate_assessments"])
        )


if __name__ == "__main__":
    unittest.main()
