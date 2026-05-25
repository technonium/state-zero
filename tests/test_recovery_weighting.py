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
from creature_utils import parse_creature_payload


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
                debug_payload = json.loads(
                    (orchestrator.output_dir / "video_prompt_validation_debug.json").read_text(encoding="utf-8")
                )

        self.assertIn('contains "chamber"', first_retry_prompt)
        self.assertFalse((orchestrator.output_dir / "last_prompt_video_retry_2.txt").exists())
        self.assertEqual(debug_payload["selected_source"], "deterministic_repair_sentence_two")
        self.assertIn("stressed formation fractures", result)
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
                    "A bright skylight opens above the sealed view. Fine ash drifts softly through the frame. Lens bloom holds on the brightest edge.",
                    "A bright skylight opens above the sealed view. Fine ash drifts softly through the frame. Lens bloom holds on the brightest edge.",
                ])
                orchestrator.call_llm = lambda prompt: next(responses)
                (orchestrator.output_dir / "video_prompt.txt").write_text("stale prompt", encoding="utf-8")

                daily_data = {
                    "depth_level": "ABYSS",
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

        self.assertIn("Video prompt validation failed after 2 LLM attempts and deterministic repair", str(ctx.exception))
        self.assertIn("abyss_bright_opening", str(ctx.exception))

    def test_plasma_low_video_prompt_repairs_materiality_after_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                responses = iter([
                    "The camera holds still, fixed witnessing frame. A pressure wave cracks the plasma core, debris of superheated gas fragmenting outward in visible ruptures across the nebula field. Film grain present throughout, lens bloom at the core.",
                    "The camera holds still, fixed witnessing frame. A pressure wave cracks the plasma core, debris of superheated gas fragmenting outward in visible ruptures across the nebula field. Film grain present throughout, lens bloom at the core.",
                ])
                orchestrator.call_llm = lambda prompt: next(responses)

                daily_data = {
                    "depth_level": "SURFACE",
                    "energy_zone": "HIGH",
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
                    "Plasma/Nebula — materiality repair test",
                    "Option A",
                )
                debug_payload = json.loads(
                    (orchestrator.output_dir / "video_prompt_validation_debug.json").read_text(encoding="utf-8")
                )

        self.assertEqual(debug_payload["selected_source"], "deterministic_repair_sentence_two")
        self.assertIn("pressure wave releases from the core", result)
        self.assertIn("gas shearing outward", result)
        for forbidden in ("crack", "fracture", "shatter", "rupture", "debris"):
            self.assertNotIn(forbidden, result.lower())

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
    @staticmethod
    def _image_fragment_kwargs():
        return {
            "fragment_phrase": "",
            "fragment_grounding": "",
        }

    def test_image_json_validator_rejects_literal_animal_and_humanoid_language(self):
        orchestrator = PromptOrchestrator.__new__(PromptOrchestrator)
        image_json = {
            "core_concept": "Volcanic formations carry lean predatory tension around a readable face in the rock.",
            "lighting": {"time": "Lateral ember light only."},
            "composition": {"sky": "None visible."},
            "color_palette": {"sky_gradient": "No open sky."},
            "creature_integration": {
                "blend": "Option A - Sculptural",
                "visibility": "Geological formations barely evoke boundary pressure through natural erosion patterns.",
            },
            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
        }

        reasons = orchestrator._validate_image_json(
            image_json,
            "DEEP",
            creature_name="Jackal",
            fragment_phrase="",
            fragment_required=False,
        )

        self.assertTrue(
            any(reason.startswith("positive_literalizing_language:core_concept:") for reason in reasons),
            reasons,
        )

    def test_image_json_validator_flags_anatomy_word_outside_visibility(self):
        orchestrator = PromptOrchestrator.__new__(PromptOrchestrator)
        image_json = {
            "core_concept": "Volcanic formations stalking the horizon with predator-tense ridges and jaw-like fissures.",
            "lighting": {"time": "Lateral ember light only."},
            "composition": {"sky": "None visible."},
            "color_palette": {"sky_gradient": "No open sky."},
            "creature_integration": {
                "blend": "Option A - Sculptural",
                "visibility": "Geological formations barely evoke boundary pressure through natural erosion patterns.",
            },
            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
        }

        reasons = orchestrator._validate_image_json(
            image_json,
            "DEEP",
            creature_name="Jackal",
            fragment_phrase="",
            fragment_required=False,
        )

        self.assertTrue(
            any(reason.startswith("positive_anatomy_outside_visibility:core_concept:") for reason in reasons),
            reasons,
        )

    def test_image_json_validator_permits_anatomy_inside_visibility(self):
        orchestrator = PromptOrchestrator.__new__(PromptOrchestrator)
        image_json = {
            "core_concept": "Volcanic formations press across cracked rock with lateral ember light.",
            "lighting": {"time": "Lateral ember light only."},
            "composition": {"sky": "None visible."},
            "color_palette": {"sky_gradient": "No open sky."},
            "creature_integration": {
                "blend": "Option A - Sculptural",
                "visibility": "Erosion patterns barely suggest a low jaw-line softened by mineral seams.",
            },
            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
        }

        reasons = orchestrator._validate_image_json(
            image_json,
            "DEEP",
            creature_name="Jackal",
            fragment_phrase="",
            fragment_required=False,
        )

        self.assertFalse(
            any(reason.startswith("positive_anatomy_outside_visibility:") for reason in reasons),
            reasons,
        )

    def test_image_json_validator_allows_geological_rock_face_language(self):
        orchestrator = PromptOrchestrator.__new__(PromptOrchestrator)
        image_json = {
            "core_concept": "Volcanic formations press across a cracked rock face with lateral ember light.",
            "lighting": {"time": "Lateral ember light only."},
            "composition": {"sky": "None visible."},
            "color_palette": {"sky_gradient": "No open sky."},
            "creature_integration": {
                "blend": "Option A - Sculptural",
                "visibility": "Geological formations barely evoke boundary pressure through natural erosion patterns.",
            },
            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
        }

        reasons = orchestrator._validate_image_json(
            image_json,
            "DEEP",
            creature_name="Jackal",
            fragment_phrase="",
            fragment_required=False,
        )

        self.assertFalse(
            any(reason.startswith("positive_literalizing_language:core_concept:face") for reason in reasons),
            reasons,
        )

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
                            "creature_integration": {"blend": "Option B - Sculptural", "visibility": "Compressed cave masses hold indirect boundary tension."},
                            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
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
                            "creature_integration": {"blend": "Option B - Sculptural", "visibility": "Compressed cave masses hold indirect boundary tension."},
                            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
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
                    **self._image_fragment_kwargs(),
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
                        "creature_integration": {"blend": "Option B - Sculptural", "visibility": "Compressed cave masses hold indirect boundary tension."},
                        "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
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
                        **self._image_fragment_kwargs(),
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
                            "creature_integration": {"blend": "Option B - Sculptural", "visibility": "Compressed cave masses hold indirect boundary tension."},
                            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
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
                    **self._image_fragment_kwargs(),
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
                        **self._image_fragment_kwargs(),
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
                            "creature_integration": {"blend": "Option A - Default", "visibility": "Sealed stone masses hold indirect boundary tension."},
                            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
                        }
                    ),
                    json.dumps(
                        {
                            "core_concept": "Sealed within the buried cave core under compressive stone mass.",
                            "lighting": {"time": "Interior pressure-light pulses through hairline seams."},
                            "composition": {"background": "Solid enclosed stone from all sides.", "sky": "None visible."},
                            "color_palette": {"sky_gradient": "No sky; interior mineral pressure glow only."},
                            "creature_integration": {"blend": "Option A - Default", "visibility": "Sealed stone masses hold indirect boundary tension."},
                            "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
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
                    **self._image_fragment_kwargs(),
                )

                retry_prompt = (orchestrator.output_dir / "last_prompt_image_json_retry.txt").read_text(encoding="utf-8")

        self.assertIn('contains "cave mouth"', retry_prompt)
        self.assertIn("ABYSS must stay sealed and interior", retry_prompt)
        self.assertIn("Sealed within the buried cave core", result["core_concept"])
        self.assertNotIn("horizon line", json.dumps(result).lower())


class CreatureFragmentRegressionTests(unittest.TestCase):
    @staticmethod
    def _recent_creature_context():
        return {
            "db_lookup_status": "ok",
            "db_lookup_error": None,
            "raw_recent_history": [],
            "normalized_banned_names": [],
            "recent_creature_names": [],
            "recent_creatures_prompt": "None — no restriction.",
            "banned_recent_names": set(),
        }

    def test_parse_creature_payload_handles_fenced_plain_and_partial_json(self):
        fenced = """```json
{"name":"Phoenix","reason":"Fire pressure.","signature_fragment":"hooked beak ridge","why_unique":"The hooked bill edge is a small external marker."}
```"""
        plain = '{"name":"Dragon","reason":"Ancient pressure.","signature_fragment":"serrated dorsal ridge","why_unique":"The serrated ridge is the smallest outward cue."}'
        partial = '{"name":"Octopus","reason":"Fluid tension."'

        self.assertEqual(parse_creature_payload(fenced)["name"], "Phoenix")
        self.assertEqual(parse_creature_payload(plain)["name"], "Dragon")
        self.assertEqual(parse_creature_payload(partial), {})

    def test_generate_creature_repairs_fragment_and_saves_source(self):
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
                            "name": "Phoenix",
                            "reason": "Solar pressure makes the archetype feel charged and reborn.",
                            "signature_fragment": "ember scar",
                            "why_unique": "It symbolizes rebirth through fire.",
                        }
                    ),
                    json.dumps(
                        {
                            "signature_fragment": "curved crest plume",
                            "why_unique": "The rising feathered crest is a small external marker that stays recognizable without needing the whole bird.",
                        }
                    ),
                ])
                orchestrator.call_llm = lambda prompt: next(responses)
                orchestrator._resolve_recent_creatures = lambda run_date: self._recent_creature_context()

                creature = orchestrator.generate_creature({"date": "2026-03-15"}, "Interpretation text.")
                fragment_payload = json.loads((orchestrator.output_dir / "creature_fragment.json").read_text(encoding="utf-8"))
                debug_payload = json.loads((orchestrator.output_dir / "creature_selection_debug.json").read_text(encoding="utf-8"))

        self.assertEqual(creature.split("—")[0].strip(), "Phoenix")
        self.assertEqual(fragment_payload["source"], "fragment_repair")
        self.assertEqual(fragment_payload["signature_fragment"], "curved crest plume")
        self.assertTrue(debug_payload["fragment_repair_attempted"])
        self.assertEqual(debug_payload["winning_fragment_source"], "fragment_repair")

    def test_generate_creature_resets_fragment_state_and_degrades_to_no_fragment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-16"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator._resolve_recent_creatures = lambda run_date: self._recent_creature_context()

                first_responses = iter([
                    json.dumps(
                        {
                            "name": "Naga",
                            "reason": "Coiling pressure suits the day.",
                            "signature_fragment": "hooked scale ridge",
                            "why_unique": "The hooked ridge along the scutes is a small external marker native to this serpent form.",
                        }
                    )
                ])
                orchestrator.call_llm = lambda prompt: next(first_responses)
                orchestrator.generate_creature({"date": "2026-03-16"}, "First interpretation.")
                self.assertEqual(orchestrator.latest_creature_fragment, "hooked scale ridge")

                second_responses = iter([
                    json.dumps(
                        {
                            "name": "Phoenix",
                            "reason": "Rising heat makes the archetype feel active.",
                            "signature_fragment": "ember scar",
                            "why_unique": "It symbolizes rebirth in flame.",
                        }
                    ),
                    json.dumps(
                        {
                            "signature_fragment": "lunar ink",
                            "why_unique": "It feels mythic and celestial.",
                        }
                    ),
                ])
                orchestrator.call_llm = lambda prompt: next(second_responses)
                orchestrator.generate_creature({"date": "2026-03-16"}, "Second interpretation.")
                fragment_payload = json.loads((orchestrator.output_dir / "creature_fragment.json").read_text(encoding="utf-8"))

        self.assertEqual(orchestrator.latest_creature_fragment, "")
        self.assertEqual(fragment_payload["source"], "no_fragment_continuation")
        self.assertFalse(fragment_payload["fragment_enabled"])

    def test_build_image_json_accepts_explicit_no_fragment_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-20"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: json.dumps(
                    {
                        "core_concept": "Buried cave walls press close in indirect stone tension.",
                        "lighting": {"time": "Cold lateral light filters through a mineral seam."},
                        "composition": {"sky": "None visible."},
                        "color_palette": {"sky_gradient": "No open sky."},
                        "creature_integration": {
                            "blend": "Option B - Sculptural",
                            "visibility": "Compressed cave masses hold indirect boundary tension.",
                        },
                        "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
                    }
                )
                orchestrator.latest_creature_fragment = "old stale fragment"
                orchestrator.latest_creature_fragment_why_unique = "old stale reason"

                result = orchestrator.build_image_json(
                    {
                        "depth_level": "DEEP",
                        "depth_keywords": ["Buried-recess"],
                        "visibility_range": "Near-field",
                        "moon_count": 0,
                        "energy_zone": "LOW",
                        "recovery_pct": 42,
                        "recovery_zone": "LOW",
                        "sleep_score_pct": 74,
                        "sleep_score_zone": "DEEP",
                        "sleep_hours": 6.2,
                        "strain": 15.4,
                        "date": "2026-03-20",
                        "date_display": "20 Mar 2026",
                        "behavior_matrix": {
                            "body_keywords": ["Wrecked"],
                            "art_keywords": ["Collapsed"],
                            "one_liner": "Total shutdown.",
                        },
                        "natal_context": {"ascendant": "Scorpio", "moon_nakshatra": "Anuradha"},
                    },
                    "A buried recess holds under pressure.",
                    "Snow Leopard",
                    "Cave Systems — explicit no-fragment test",
                    fragment_phrase="",
                    fragment_grounding="",
                )

        self.assertIn("indirect boundary tension", result["creature_integration"]["visibility"])


class PromptStageSmokeTests(unittest.TestCase):
    def test_prompt_stage_sequence_still_builds_metadata_and_video(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-21"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator._resolve_recent_creatures = lambda run_date: {
                    "db_lookup_status": "ok",
                    "db_lookup_error": None,
                    "raw_recent_history": [],
                    "normalized_banned_names": [],
                    "recent_creature_names": [],
                    "recent_creatures_prompt": "None — no restriction.",
                    "banned_recent_names": set(),
                }
                orchestrator._resolve_environment_candidates = lambda energy_zone, run_date: {
                    "full_options": ["Cave Systems — subterranean chambers"],
                    "candidate_options": ["Cave Systems — subterranean chambers"],
                    "recent_names": [],
                    "excluded_names": [],
                    "db_lookup_status": "ok",
                    "db_lookup_error": None,
                    "soft_fallback": False,
                    "candidate_source": "filtered_candidates",
                }

                def fake_llm(prompt: str) -> str:
                    if "## Output Format" in prompt and '"signature_fragment"' in prompt:
                        return json.dumps(
                            {
                                "name": "Naga",
                                "reason": "Coiling pressure suits the day.",
                                "signature_fragment": "hooked scale ridge",
                                "why_unique": "The hooked ridge along the scutes is a small external marker native to this serpent form.",
                            }
                        )
                    if "environment options" in prompt.lower():
                        return "Cave Systems — subterranean chambers"
                    if "MASTER JSON TEMPLATE" in prompt:
                        return json.dumps(
                            {
                                "core_concept": "Buried cave masses hold coiling pressure through enclosed stone.",
                                "lighting": {"time": "Cold lateral light filters through a mineral seam."},
                                "composition": {"sky": "None visible."},
                                "color_palette": {"sky_gradient": "No open sky."},
                                "creature_integration": {
                                    "blend": "Option B - Sculptural",
                                    "visibility": "Sculptural cave masses dominate, and to someone looking closely one hooked scale ridge might be half-lost in the patterning while landscape remains primary.",
                                },
                                "mandatory_exclusions": ["no literal animal, creature, beast, mascot, or character subject"],
                            }
                        )
                    if "video" in prompt.lower():
                        return "The camera holds from stillness. Mineral dust pours from a side-wall fissure for the full shot. Film grain persists while dim light bleeds laterally across the rock."
                    if "interpretation" in prompt.lower():
                        return "The day compresses attention inward while keeping perception alert."
                    return "Cave Systems — subterranean chambers"

                orchestrator.call_llm = fake_llm
                daily_data = {
                    "depth_level": "DEEP",
                    "depth_keywords": ["Buried-recess"],
                    "visibility_range": "Near-field",
                    "moon_count": 0,
                    "energy_zone": "MEDIUM",
                    "recovery_pct": 55,
                    "recovery_zone": "MEDIUM",
                    "sleep_score_pct": 78,
                    "sleep_score_zone": "DEEP",
                    "sleep_hours": 6.8,
                    "strain": 12.1,
                    "date": "2026-03-21",
                    "date_display": "21 Mar 2026",
                    "behavior_matrix": {
                        "body_keywords": ["Held", "coiled"],
                        "art_keywords": ["Balanced", "rhythmic"],
                        "one_liner": "The pressure holds but does not collapse.",
                    },
                    "natal_context": {"ascendant": "Scorpio", "moon_nakshatra": "Anuradha"},
                    "dasha": {"planets_detail": {}},
                }

                interpretation = orchestrator.generate_interpretation(daily_data)
                creature = orchestrator.generate_creature(daily_data, interpretation)
                environment = orchestrator.generate_environment(daily_data, interpretation)
                image_json = orchestrator.build_image_json(daily_data, interpretation, creature, environment)
                metadata = orchestrator.extract_metadata(daily_data, daily_data["date_display"], environment, image_json)
                video_prompt = orchestrator.build_video_prompt(daily_data, environment, "Option B")

        self.assertEqual(creature.split("—")[0].strip(), "Naga")
        self.assertEqual(environment.split("—")[0].strip(), "Cave Systems")
        self.assertIn("hooked scale ridge", image_json["creature_integration"]["visibility"])
        self.assertNotIn("Naga", json.dumps(image_json.get("mandatory_exclusions", [])))
        self.assertEqual(metadata["date_display"], "21 Mar 2026")
        self.assertIn("Mineral dust pours", video_prompt)


if __name__ == "__main__":
    unittest.main()
