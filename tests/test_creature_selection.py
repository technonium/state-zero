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

from creature_utils import format_creature_output, normalize_creature_name, split_creature_output
from database_manager import CardDatabase
from prompts import PromptOrchestrator
from pipeline import WHOOPPipeline


class CreatureSelectionTests(unittest.TestCase):
    def _seed_creature_history(self, db: CardDatabase, creatures: list[tuple[str, str]]):
        for run_date, creature in creatures:
            db.insert_card(
                {
                    "date": run_date,
                    "title": f"{creature} Title",
                    "scene_description": "scene",
                    "environment": "Frozen/Ice — quiet reason",
                    "environment_name": "Frozen/Ice",
                    "environment_reason": "quiet reason",
                    "creature": creature,
                    "energy_zone": "LOW",
                    "image_path": "/tmp/image.png",
                    "video_path": "/tmp/video.mp4",
                    "instagram_post_id": "real_ig_123",
                }
            )

    def test_split_creature_output_normalizes_wrapped_name(self):
        name, reason = split_creature_output('**Mantis** — sharp stillness')

        self.assertEqual(name, "Mantis")
        self.assertEqual(reason, "sharp stillness")
        self.assertEqual(normalize_creature_name('"Mantis"'), "mantis")
        self.assertEqual(normalize_creature_name("**Mantis**"), "mantis")
        self.assertEqual(format_creature_output("Phoenix", "rebirth"), "Phoenix — rebirth")

    def test_split_creature_output_returns_empty_for_blank_text(self):
        name, reason = split_creature_output("   \n\n")

        self.assertEqual(name, "")
        self.assertEqual(reason, "")

    def test_split_creature_output_handles_choice_prefixes_and_tight_dash(self):
        self.assertEqual(
            split_creature_output("I choose Phoenix — clean rebirth"),
            ("Phoenix", "clean rebirth"),
        )
        self.assertEqual(
            split_creature_output("Selected: Phoenix — clean rebirth"),
            ("Phoenix", "clean rebirth"),
        )
        self.assertEqual(
            split_creature_output("Mantis—stillness"),
            ("Mantis", "stillness"),
        )
        self.assertEqual(
            split_creature_output("Spider-Man — agile boundary crosser"),
            ("Spider-Man", "agile boundary crosser"),
        )
        self.assertEqual(
            split_creature_output("Lion-man hybrid — liminal threshold force"),
            ("Lion-man hybrid", "liminal threshold force"),
        )

    def test_get_recent_creature_names_returns_last_ten_and_skips_invalid_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                db = CardDatabase()
                creatures = [
                    ("2026-03-12", "Mantis — one"),
                    ("2026-03-11", "**Mantis** — two"),
                    ("2026-03-10", "Phoenix — three"),
                    ("2026-03-09", ""),
                    ("2026-03-08", "Wolf — four"),
                    ("2026-03-07", "Owl — five"),
                    ("2026-03-06", "Jackal — six"),
                    ("2026-03-05", "Raven — seven"),
                    ("2026-03-04", "Peacock — eight"),
                    ("2026-03-03", "Scorpion — nine"),
                    ("2026-03-02", "Octopus — ten"),
                    ("2026-03-01", "Parrot — eleven"),
                ]
                self._seed_creature_history(db, creatures)

                names = db.get_recent_creature_names("2026-03-13", limit=10)

        self.assertEqual(
            names,
            ["Mantis", "Mantis", "Phoenix", "Wolf", "Owl", "Jackal", "Raven", "Peacock", "Scorpion"],
        )

    def test_recent_creature_names_exclude_mock_posts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                db = CardDatabase()
                self._seed_creature_history(
                    db,
                    [
                        ("2026-03-12", "Mantis — real"),
                        ("2026-03-11", "Scorpion — real"),
                    ],
                )
                db.insert_card(
                    {
                        "date": "2026-03-10",
                        "title": "Mock Title",
                        "scene_description": "scene",
                        "environment": "Frozen/Ice — quiet reason",
                        "environment_name": "Frozen/Ice",
                        "environment_reason": "quiet reason",
                        "creature": "Phoenix — mock",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "instagram_post_id": "mock_ig_12345",
                    }
                )

                names = db.get_recent_creature_names("2026-03-13", limit=10)

        self.assertEqual(names, ["Mantis", "Scorpion"])

    def test_recent_creature_names_exclude_empty_post_ids(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": tmpdir}, clear=False):
                db = CardDatabase()
                self._seed_creature_history(db, [("2026-03-12", "Mantis — real")])
                db.insert_card(
                    {
                        "date": "2026-03-11",
                        "title": "Empty Post Id Title",
                        "scene_description": "scene",
                        "environment": "Frozen/Ice — quiet reason",
                        "environment_name": "Frozen/Ice",
                        "environment_reason": "quiet reason",
                        "creature": "Phoenix — skipped",
                        "energy_zone": "LOW",
                        "image_path": "/tmp/image.png",
                        "video_path": "/tmp/video.mp4",
                        "instagram_post_id": "",
                    }
                )

                names = db.get_recent_creature_names("2026-03-13", limit=10)

        self.assertEqual(names, ["Mantis"])

    def test_generate_creature_injects_recent_names_and_accepts_unique_first_try(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                },
                clear=False,
            ):
                db = CardDatabase()
                self._seed_creature_history(
                    db,
                    [
                        ("2026-03-12", "**Mantis** — one"),
                        ("2026-03-11", "Scorpion — two"),
                        ("2026-03-10", "Mantis — three"),
                    ],
                )
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                prompts_seen = []

                def fake_call(prompt: str) -> str:
                    prompts_seen.append(prompt)
                    return "Phoenix — clean rebirth"

                orchestrator.call_llm = fake_call
                result = orchestrator.generate_creature(
                    {
                        "date": "2026-03-13",
                        "dasha": {},
                        "natal_context": {},
                    },
                    "quiet interpretation",
                )
                prompt_text = (orchestrator.output_dir / "last_prompt_creature.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "creature_selection_debug.json").read_text(encoding="utf-8")
                )
                selected_text = (orchestrator.output_dir / "creature_selected.txt").read_text(encoding="utf-8")

        self.assertEqual(result, "Phoenix — clean rebirth")
        self.assertIn("## Recent Creatures (Do Not Repeat)", prompt_text)
        self.assertIn("- Mantis", prompt_text)
        self.assertIn("- Scorpion", prompt_text)
        self.assertEqual(prompt_text.count("- Mantis"), 1)
        self.assertEqual(len(prompts_seen), 1)
        self.assertEqual(debug_payload["final_selection_source"], "llm_valid")
        self.assertEqual(selected_text, "Phoenix — clean rebirth")

    def test_generate_creature_renders_none_when_history_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: "Phoenix — clean rebirth"

                orchestrator.generate_creature(
                    {
                        "date": "2026-03-13",
                        "dasha": {},
                        "natal_context": {},
                    },
                    "quiet interpretation",
                )
                prompt_text = (orchestrator.output_dir / "last_prompt_creature.txt").read_text(encoding="utf-8")

        self.assertIn("None — no restriction.", prompt_text)

    def test_generate_creature_retries_once_on_repeat_and_accepts_unique_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                },
                clear=False,
            ):
                db = CardDatabase()
                self._seed_creature_history(db, [("2026-03-12", "Mantis — one")])
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(["**Mantis** — repeated", "Phoenix — clean rebirth"])
                prompts_seen = []

                def fake_call(prompt: str) -> str:
                    prompts_seen.append(prompt)
                    return next(responses)

                orchestrator.call_llm = fake_call
                result = orchestrator.generate_creature(
                    {
                        "date": "2026-03-13",
                        "dasha": {},
                        "natal_context": {},
                    },
                    "quiet interpretation",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "creature_selection_debug.json").read_text(encoding="utf-8")
                )
                retry_prompt = (orchestrator.output_dir / "last_prompt_creature_retry.txt").read_text(encoding="utf-8")
                selected_text = (orchestrator.output_dir / "creature_selected.txt").read_text(encoding="utf-8")

        self.assertEqual(result, "Phoenix — clean rebirth")
        self.assertEqual(len(prompts_seen), 2)
        self.assertIn('Your previous choice "Mantis" is invalid', retry_prompt)
        self.assertEqual(debug_payload["retry_triggered"], True)
        self.assertEqual(debug_payload["final_selection_source"], "corrective_retry_valid")
        self.assertEqual(selected_text, "Phoenix — clean rebirth")

    def test_generate_creature_warns_after_failed_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                },
                clear=False,
            ):
                db = CardDatabase()
                self._seed_creature_history(db, [("2026-03-12", "Mantis — one")])
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(["Mantis — repeated", "Mantis — repeated again"])
                orchestrator.call_llm = lambda prompt: next(responses)

                result = orchestrator.generate_creature(
                    {
                        "date": "2026-03-13",
                        "dasha": {},
                        "natal_context": {},
                    },
                    "quiet interpretation",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "creature_selection_debug.json").read_text(encoding="utf-8")
                )
                selected_text = (orchestrator.output_dir / "creature_selected.txt").read_text(encoding="utf-8")

        self.assertEqual(result, "Mantis — repeated again")
        self.assertEqual(debug_payload["final_selection_source"], "repeat_after_retry_warning")
        self.assertEqual(debug_payload["retained_parseable_source"], "retry")
        self.assertEqual(selected_text, "Mantis — repeated again")

    def test_generate_creature_preserves_first_parseable_output_when_retry_is_unparseable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                },
                clear=False,
            ):
                db = CardDatabase()
                self._seed_creature_history(db, [("2026-03-12", "Mantis — one")])
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(["I choose Mantis — repeated", "   \n"])
                orchestrator.call_llm = lambda prompt: next(responses)

                result = orchestrator.generate_creature(
                    {
                        "date": "2026-03-13",
                        "dasha": {},
                        "natal_context": {},
                    },
                    "quiet interpretation",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "creature_selection_debug.json").read_text(encoding="utf-8")
                )
                selected_text = (orchestrator.output_dir / "creature_selected.txt").read_text(encoding="utf-8")

        self.assertEqual(result, "Mantis — repeated")
        self.assertEqual(debug_payload["final_selection_source"], "repeat_after_retry_warning")
        self.assertEqual(debug_payload["retained_parseable_source"], "first")
        self.assertEqual(selected_text, "Mantis — repeated")

    def test_generate_creature_retries_when_first_output_is_unparseable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(["   \n", "Phoenix — clean rebirth"])
                orchestrator.call_llm = lambda prompt: next(responses)

                result = orchestrator.generate_creature(
                    {
                        "date": "2026-03-13",
                        "dasha": {},
                        "natal_context": {},
                    },
                    "quiet interpretation",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "creature_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, "Phoenix — clean rebirth")
        self.assertEqual(debug_payload["final_selection_source"], "corrective_retry_valid")

    def test_generate_creature_fails_when_both_attempts_are_unparseable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(["   \n", "   \n"])
                orchestrator.call_llm = lambda prompt: next(responses)

                with self.assertRaisesRegex(RuntimeError, "both attempts were unparseable"):
                    orchestrator.generate_creature(
                        {
                            "date": "2026-03-13",
                            "dasha": {},
                            "natal_context": {},
                        },
                        "quiet interpretation",
                    )

    def test_generate_creature_continues_when_history_lookup_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: "Phoenix — clean rebirth"

                with patch("prompts.CardDatabase.get_recent_creature_names", side_effect=RuntimeError("db down")):
                    result = orchestrator.generate_creature(
                        {
                            "date": "2026-03-13",
                            "dasha": {},
                            "natal_context": {},
                        },
                        "quiet interpretation",
                    )
                prompt_text = (orchestrator.output_dir / "last_prompt_creature.txt").read_text(encoding="utf-8")
                debug_payload = json.loads(
                    (orchestrator.output_dir / "creature_selection_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(result, "Phoenix — clean rebirth")
        self.assertIn("None — no restriction.", prompt_text)
        self.assertEqual(debug_payload["db_lookup_status"], "failed")
        self.assertEqual(debug_payload["final_selection_source"], "history_lookup_failed_no_guard")

    def test_pipeline_prefers_selected_prompt_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-13",
                    "PIPELINE_POST_TO_INSTAGRAM": "false",
                },
                clear=False,
            ):
                pipeline = WHOOPPipeline()
                pipeline.output_dir.mkdir(parents=True, exist_ok=True)
                (pipeline.output_dir / "blend_option.txt").write_text("Option B", encoding="utf-8")
                (pipeline.output_dir / "creature.txt").write_text("I choose Phoenix — noisy", encoding="utf-8")
                (pipeline.output_dir / "creature_selected.txt").write_text("Phoenix — canonical", encoding="utf-8")
                (pipeline.output_dir / "environment.txt").write_text("I choose Frozen/Ice — noisy", encoding="utf-8")
                (pipeline.output_dir / "environment_selected.txt").write_text("Frozen/Ice — canonical", encoding="utf-8")

                blend_option, creature, environment = pipeline._load_required_text_outputs()

        self.assertEqual(blend_option, "Option B")
        self.assertEqual(creature, "Phoenix — canonical")
        self.assertEqual(environment, "Frozen/Ice — canonical")


if __name__ == "__main__":
    unittest.main()
