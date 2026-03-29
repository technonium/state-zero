import os
import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from prompts import PromptOrchestrator


class RecoveryPromptRegressionTests(unittest.TestCase):
    def test_json_builder_contains_recovery_severity_mapping(self):
        content = (PROJECT_ROOT / "src/prompts/json_builder.md").read_text(encoding="utf-8")
        self.assertIn("## RECOVERY SEVERITY MAPPING", content)
        self.assertIn("**HIGH recovery:** intact, coherent, supported, stable, breathable", content)
        self.assertIn(
            "**LOW recovery:** fractured, depleted, stripped, cooling, pressure-stressed, partially failed, post-event",
            content,
        )
        self.assertIn("Recovery does NOT change the scene's geometry.", content)
        self.assertIn("LOW recovery does not read pristine, elegant, untouched, or serene", content)
        self.assertIn("Recovery changes world condition, not scene geometry", content)

    def test_video_prompt_contains_recovery_guidance(self):
        content = (PROJECT_ROOT / "src/prompts/video.md").read_text(encoding="utf-8")
        self.assertIn("- **Recovery Zone:** {recovery_zone}", content)
        self.assertIn("- **Body Keywords:** {body_keywords}", content)
        self.assertIn("## Recovery Severity Interpretation", content)
        self.assertIn(
            "**LOW recovery** — the environment is showing visible strain or active failure.",
            content,
        )
        self.assertIn("Use the LOW recovery event from the environment table above", content)
        self.assertIn(
            "sentence 2 must describe the LOW recovery event for the given environment",
            content,
        )
        self.assertIn(
            "If recovery_zone is LOW: scene must not feel graceful, pristine, healthy, or untouched.",
            content,
        )
        self.assertIn("These are four equal options — not a ranked list", content)
        self.assertIn("Zoom is not the default", content)
        self.assertIn("never through luminance decay over time", content)
        self.assertIn("The last frame must be at least as readable as the first", content)
        self.assertIn("static hold, no movement, fixed axis", content)
        self.assertIn("For ABYSS, the camera must not be aimed upward toward any bright zone.", content)


class VideoPromptWiringTests(unittest.TestCase):
    def test_build_video_prompt_injects_recovery_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = (
                    lambda prompt: "The camera holds from stillness. "
                    "A fracture widens through the sealed floor and mineral dust pours from the breach for the full shot. "
                    "Interior glow stays pinned to the pressure seams."
                )

                daily_data = {
                    "depth_level": "ABYSS",
                    "energy_zone": "LOW",
                    "recovery_zone": "LOW",
                    "moon_count": 1,
                    "behavior_matrix": {
                        "body_keywords": ["Destroyed", "void", "primal"],
                        "art_keywords": ["Crushing", "devastated", "primordial"],
                        "one_liner": "Complete system failure. Nothing left.",
                    },
                }

                result = orchestrator.build_video_prompt(
                    daily_data,
                    "Glacial Valley — sealed test reason",
                    "Option A",
                )

                prompt_text = (orchestrator.output_dir / "last_prompt_video.txt").read_text(encoding="utf-8")
                saved_output = (orchestrator.output_dir / "video_prompt.txt").read_text(encoding="utf-8")

        self.assertIn("fracture widens", result)
        self.assertEqual(saved_output, result)
        self.assertIn("- **Recovery Zone:** LOW", prompt_text)
        self.assertIn("- **Body Keywords:** Destroyed, void, primal", prompt_text)
        self.assertIn("- **Behavioral One-liner:** Complete system failure. Nothing left.", prompt_text)

    def test_low_recovery_retries_until_sentence_two_has_failure_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    "The camera holds still. Fine ash drifts softly through the frame. Lens bloom hangs over the brightest seam.",
                    "The camera holds still. A fissure ruptures along the formation base and ash pours from the breach for the full shot. Lens bloom hangs over the brightest seam.",
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "DEEP",
                    "energy_zone": "LOW",
                    "recovery_zone": "LOW",
                    "moon_count": 0,
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                }

                result = orchestrator.build_video_prompt(
                    daily_data,
                    "Volcanic — retry test",
                    "Option A",
                )

                retry_prompt = (orchestrator.output_dir / "last_prompt_video_retry.txt").read_text(encoding="utf-8")

        self.assertIn("specifically in sentence 2", retry_prompt)
        self.assertIn("Previous response:", retry_prompt)
        self.assertIn("Fine ash drifts softly through the frame.", retry_prompt)
        self.assertIn("fissure ruptures", result)

    def test_deep_recovery_retry_output_is_revalidated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    "The camera watches from stillness. A chamber wall cracks and dust falls in thin streams. Lens bloom holds on the brightest edge.",
                    "The camera watches from stillness. Fine ash drifts softly through the frame. Lens bloom holds on the brightest edge.",
                    "The camera watches from stillness. A rock shelf collapses and mineral dust cascades from the buried recess for the full shot. Lens bloom holds on the brightest edge.",
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "DEEP",
                    "energy_zone": "LOW",
                    "recovery_zone": "LOW",
                    "moon_count": 0,
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                }

                result = orchestrator.build_video_prompt(
                    daily_data,
                    "Cave Systems — retry test",
                    "Option A",
                )

                first_retry_prompt = (orchestrator.output_dir / "last_prompt_video_retry.txt").read_text(encoding="utf-8")
                second_retry_prompt = (orchestrator.output_dir / "last_prompt_video_retry_2.txt").read_text(encoding="utf-8")

        self.assertIn('contains "chamber"', first_retry_prompt)
        self.assertIn("specifically in sentence 2", second_retry_prompt)
        self.assertIn("rock shelf collapses", result)
        self.assertNotIn("chamber", result.lower())

    def test_deep_recovery_retry_rejects_overhead_aperture_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    "The camera holds from stillness. Dust pours while a bright shaft falls through a hole above for the full shot. Lens bloom clings to the opening.",
                    "The camera holds from stillness. Mineral dust pours from a side-wall fissure for the full shot. Lens bloom stays pinned to the lateral seam.",
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "DEEP",
                    "energy_zone": "LOW",
                    "recovery_zone": "LOW",
                    "moon_count": 0,
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                }

                result = orchestrator.build_video_prompt(
                    daily_data,
                    "Cave Systems — overhead aperture retry test",
                    "Option A",
                )

                retry_prompt = (orchestrator.output_dir / "last_prompt_video_retry.txt").read_text(encoding="utf-8")

        self.assertIn('contains "shaft"', retry_prompt)
        self.assertIn("must not show a centered overhead opening", retry_prompt)
        self.assertIn("side-wall fissure", result)
        self.assertNotIn("hole above", result.lower())

    def test_retry_exhaustion_raises_and_does_not_write_video_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    "The camera watches from stillness. Fine ash drifts softly through the frame. Lens bloom holds on the brightest edge.",
                    "The camera watches from stillness. Fine ash drifts softly through the frame. Lens bloom holds on the brightest edge.",
                    "The camera watches from stillness. Fine ash drifts softly through the frame. Lens bloom holds on the brightest edge.",
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "DEEP",
                    "energy_zone": "LOW",
                    "recovery_zone": "LOW",
                    "moon_count": 0,
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                }

                with self.assertRaises(RuntimeError) as ctx:
                    orchestrator.build_video_prompt(
                        daily_data,
                        "Cave Systems — retry exhaustion test",
                        "Option A",
                    )

                output_path = orchestrator.output_dir / "video_prompt.txt"
                self.assertFalse(output_path.exists())

        self.assertIn("Video prompt validation failed after 3 attempts", str(ctx.exception))
        self.assertIn("low_recovery_sentence_two_no_failure_event", str(ctx.exception))

    def test_mock_mode_video_prompt_still_produces_valid_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-15",
                    "OPENROUTER_API_KEY": "",
                    "GOOGLE_API_KEY_PRIMARY": "mock",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock", openrouter_api_key="")

                daily_data = {
                    "depth_level": "DEEP",
                    "energy_zone": "LOW",
                    "recovery_zone": "LOW",
                    "moon_count": 0,
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                }

                result = orchestrator.build_video_prompt(
                    daily_data,
                    "Cave Systems — mock mode test",
                    "Option A",
                )

                saved_output = (orchestrator.output_dir / "video_prompt.txt").read_text(encoding="utf-8")

        self.assertEqual(saved_output, result)
        self.assertIn("side-wall fissure", result)

    def test_abyss_retry_rejects_bright_upper_opening_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    "The camera holds from stillness. A bright skylight opens above the cave mouth while the tunnel exit glows against a pale horizon. Cold light stays pinned to the upper opening.",
                    "The camera holds from stillness. Pressure shimmer crawls through the sealed dark and the enclosing surfaces remain unreadably interior. Cold light stays trapped in the mineral haze.",
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "ABYSS",
                    "energy_zone": "LOW",
                    "recovery_zone": "MEDIUM",
                    "moon_count": 0,
                    "behavior_matrix": {
                        "body_keywords": ["Crushed", "void", "sealed"],
                        "art_keywords": ["Buried", "lightless", "oppressive"],
                        "one_liner": "The world remains sealed shut.",
                    },
                }

                result = orchestrator.build_video_prompt(
                    daily_data,
                    "Cave Systems — abyss opening retry test",
                    "Option A",
                )

                retry_prompt = (orchestrator.output_dir / "last_prompt_video_retry.txt").read_text(encoding="utf-8")

        self.assertIn('contains "skylight"', retry_prompt)
        self.assertIn("ABYSS must not show a cave mouth, skylight, tunnel exit, horizon line", retry_prompt)
        self.assertIn("sealed dark", result)
        self.assertNotIn("skylight", result.lower())


class ImagePromptWiringTests(unittest.TestCase):
    def test_build_image_json_retries_until_deep_output_removes_overhead_aperture_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    json.dumps(
                        {
                            "core_concept": "Inside a buried cave chamber with a bright opening above.",
                            "lighting": {
                                "time": "Filtered light descends through a shaft above.",
                                "atmosphere": "Dust hangs in the beam.",
                            },
                            "composition": {"sky": "A pale upper opening is visible."},
                            "color_palette": {"sky_gradient": "Cold white shaft glow."},
                            "creature_integration": {"blend": "Option B - Sculptural"},
                        }
                    ),
                    json.dumps(
                        {
                            "core_concept": "Inside a buried cave recess with overhead rock mass pressing close above.",
                            "lighting": {
                                "time": "Cold light seeps laterally through a side-wall mineral seam.",
                                "atmosphere": "Dust stays pinned to the lateral seam.",
                            },
                            "composition": {"sky": "None visible."},
                            "color_palette": {"sky_gradient": "Omit direct sky glow."},
                            "creature_integration": {"blend": "Option B - Sculptural"},
                        }
                    ),
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "DEEP",
                    "depth_keywords": ["Buried-recess", "Overhead-mass", "Filtered-light"],
                    "visibility_range": "Near-field",
                    "moon_count": 0,
                    "energy_zone": "LOW",
                    "recovery_pct": 42,
                    "recovery_zone": "LOW",
                    "sleep_score_pct": 74,
                    "sleep_score_zone": "DEEP",
                    "sleep_hours": 6.2,
                    "strain": 15.4,
                    "date": "2026-03-15",
                    "date_display": "15 Mar 2026",
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                    "natal_context": {"ascendant": "Scorpio", "moon_nakshatra": "Anuradha"},
                }

                result = orchestrator.build_image_json(
                    daily_data,
                    "A buried recess holds under pressure.",
                    "Snow Leopard",
                    "Cave Systems — deep retry test",
                )

                retry_prompt = (orchestrator.output_dir / "last_prompt_image_json_retry.txt").read_text(encoding="utf-8")
                saved_output = json.loads((orchestrator.output_dir / "image_prompt.json").read_text(encoding="utf-8"))

        self.assertIn('contains "chamber"', retry_prompt)
        self.assertIn("must not show a centered overhead opening", retry_prompt)
        self.assertEqual(saved_output, result)
        self.assertIn("side-wall mineral seam", result["lighting"]["time"])
        self.assertNotIn("opening above", json.dumps(result).lower())

    def test_build_image_json_retry_exhaustion_raises_and_does_not_write_image_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                bad_output = json.dumps(
                    {
                        "core_concept": "Inside a buried cave chamber with a bright opening above.",
                        "lighting": {"time": "A shaft falls from above."},
                        "composition": {"sky": "Upper opening remains visible."},
                        "color_palette": {"sky_gradient": "Cold shaft glow."},
                        "creature_integration": {"blend": "Option B - Sculptural"},
                    }
                )
                responses = iter([bad_output, bad_output, bad_output])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "DEEP",
                    "depth_keywords": ["Buried-recess", "Overhead-mass", "Filtered-light"],
                    "visibility_range": "Near-field",
                    "moon_count": 0,
                    "energy_zone": "LOW",
                    "recovery_pct": 42,
                    "recovery_zone": "LOW",
                    "sleep_score_pct": 74,
                    "sleep_score_zone": "DEEP",
                    "sleep_hours": 6.2,
                    "strain": 15.4,
                    "date": "2026-03-15",
                    "date_display": "15 Mar 2026",
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                    "natal_context": {"ascendant": "Scorpio", "moon_nakshatra": "Anuradha"},
                }

                with self.assertRaises(RuntimeError) as ctx:
                    orchestrator.build_image_json(
                        daily_data,
                        "A buried recess holds under pressure.",
                        "Snow Leopard",
                        "Cave Systems — deep retry exhaustion test",
                    )

                output_path = orchestrator.output_dir / "image_prompt.json"
                self.assertFalse(output_path.exists())

        self.assertIn("Image JSON validation failed after 3 attempts", str(ctx.exception))
        self.assertIn("deep_image_overhead_aperture", str(ctx.exception))

    def test_build_image_json_retries_after_parse_failure_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    "this is not valid json",
                    json.dumps(
                        {
                            "core_concept": "Inside a buried cave recess with overhead rock mass pressing close above.",
                            "lighting": {"time": "Cold light enters laterally through a side-wall seam."},
                            "composition": {"sky": "None visible."},
                            "color_palette": {"sky_gradient": "Enclosed interior, no direct sky glow."},
                            "creature_integration": {"blend": "Option B - Sculptural"},
                        }
                    ),
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "DEEP",
                    "depth_keywords": ["Buried-recess", "Overhead-mass", "Filtered-light"],
                    "visibility_range": "Near-field",
                    "moon_count": 0,
                    "energy_zone": "LOW",
                    "recovery_pct": 42,
                    "recovery_zone": "LOW",
                    "sleep_score_pct": 74,
                    "sleep_score_zone": "DEEP",
                    "sleep_hours": 6.2,
                    "strain": 15.4,
                    "date": "2026-03-15",
                    "date_display": "15 Mar 2026",
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                    "natal_context": {"ascendant": "Scorpio", "moon_nakshatra": "Anuradha"},
                }

                result = orchestrator.build_image_json(
                    daily_data,
                    "A buried recess holds under pressure.",
                    "Snow Leopard",
                    "Cave Systems — parse recovery test",
                )

                retry_prompt = (orchestrator.output_dir / "last_prompt_image_json_retry.txt").read_text(encoding="utf-8")
                saved_output = json.loads((orchestrator.output_dir / "image_prompt.json").read_text(encoding="utf-8"))

        self.assertIn("was not valid JSON", retry_prompt)
        self.assertEqual(saved_output, result)
        self.assertIn("side-wall seam", result["lighting"]["time"])

    def test_build_image_json_parse_failure_exhaustion_raises_and_writes_no_image_prompt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter(["not json", "still not json", "again not json"])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "DEEP",
                    "depth_keywords": ["Buried-recess", "Overhead-mass", "Filtered-light"],
                    "visibility_range": "Near-field",
                    "moon_count": 0,
                    "energy_zone": "LOW",
                    "recovery_pct": 42,
                    "recovery_zone": "LOW",
                    "sleep_score_pct": 74,
                    "sleep_score_zone": "DEEP",
                    "sleep_hours": 6.2,
                    "strain": 15.4,
                    "date": "2026-03-15",
                    "date_display": "15 Mar 2026",
                    "behavior_matrix": {
                        "body_keywords": ["Wrecked", "shutdown", "leaden"],
                        "art_keywords": ["Collapsed", "shattered", "suffocating"],
                        "one_liner": "Total shutdown.",
                    },
                    "natal_context": {"ascendant": "Scorpio", "moon_nakshatra": "Anuradha"},
                }

                with self.assertRaises(RuntimeError) as ctx:
                    orchestrator.build_image_json(
                        daily_data,
                        "A buried recess holds under pressure.",
                        "Snow Leopard",
                        "Cave Systems — parse exhaustion test",
                    )

                output_path = orchestrator.output_dir / "image_prompt.json"
                self.assertFalse(output_path.exists())

        self.assertIn("Image JSON validation failed after 3 attempts", str(ctx.exception))
        self.assertIn("image_json_parse_failure", str(ctx.exception))

    def test_build_image_json_retries_for_abyss_bright_opening_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    json.dumps(
                        {
                            "core_concept": "At the cave mouth under a bright upper opening.",
                            "composition": {
                                "background": "A tunnel exit points to a horizon line.",
                                "sky": "Bright upper opening visible.",
                            },
                            "creature_integration": {"blend": "Option A - Default"},
                        }
                    ),
                    json.dumps(
                        {
                            "core_concept": "Sealed within the buried cave core under compressive stone mass.",
                            "lighting": {"time": "Interior pressure-light pulses through hairline seams."},
                            "composition": {"background": "Solid enclosed stone from all sides.", "sky": "None visible."},
                            "color_palette": {"sky_gradient": "No sky; interior mineral pressure glow only."},
                            "creature_integration": {"blend": "Option A - Default"},
                        }
                    ),
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "ABYSS",
                    "depth_keywords": ["Sealed", "Compression-fractures", "Interior-pressure", "No-above"],
                    "visibility_range": "40%",
                    "moon_count": 0,
                    "energy_zone": "LOW",
                    "recovery_pct": 12,
                    "recovery_zone": "LOW",
                    "sleep_score_pct": 60,
                    "sleep_score_zone": "ABYSS",
                    "sleep_hours": 4.2,
                    "strain": 16.1,
                    "date": "2026-03-15",
                    "date_display": "15 Mar 2026",
                    "behavior_matrix": {
                        "body_keywords": ["Crushed", "void", "sealed"],
                        "art_keywords": ["Buried", "lightless", "oppressive"],
                        "one_liner": "The world remains sealed shut.",
                    },
                    "natal_context": {"ascendant": "Scorpio", "moon_nakshatra": "Anuradha"},
                }

                result = orchestrator.build_image_json(
                    daily_data,
                    "Sealed abyss pressure-world.",
                    "Snow Leopard",
                    "Cave Systems — abyss retry test",
                )

                retry_prompt = (orchestrator.output_dir / "last_prompt_image_json_retry.txt").read_text(encoding="utf-8")

        self.assertIn('contains "cave mouth"', retry_prompt)
        self.assertIn("ABYSS must stay sealed and interior", retry_prompt)
        self.assertIn("Sealed within the buried cave core", result["core_concept"])
        self.assertNotIn("horizon line", json.dumps(result).lower())


if __name__ == "__main__":
    unittest.main()
