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
            "**LOW recovery** — burdened, brittle, stalled, post-collapse, residual failure",
            content,
        )
        self.assertIn("weighted holds, reluctant push-ins, exhausted pullbacks", content)
        self.assertIn(
            "If recovery_zone is LOW: scene must not feel graceful, pristine, healthy, or untouched.",
            content,
        )


class VideoPromptWiringTests(unittest.TestCase):
    def test_build_video_prompt_injects_recovery_inputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {"STATE_ZERO_PRIVATE_ROOT": tmpdir, "PIPELINE_DATE": "2026-03-15"},
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="mock")
                orchestrator.call_llm = lambda prompt: "video prompt output"

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

        self.assertEqual(result, "video prompt output")
        self.assertEqual(saved_output, "video prompt output")
        self.assertIn("- **Recovery Zone:** LOW", prompt_text)
        self.assertIn("- **Body Keywords:** Destroyed, void, primal", prompt_text)
        self.assertIn("- **Behavioral One-liner:** Complete system failure. Nothing left.", prompt_text)


if __name__ == "__main__":
    unittest.main()
