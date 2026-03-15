import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src/scripts"))

from lookups import get_depth_keywords


class DepthKeywordTests(unittest.TestCase):
    def test_mid_depth_keywords_follow_new_light_model(self):
        self.assertEqual(
            get_depth_keywords("MID-DEPTH"),
            ["Beneath", "Overhang", "Partial-sky", "One-direction-light"],
        )

    def test_deep_keywords_follow_chamber_model(self):
        self.assertEqual(
            get_depth_keywords("DEEP"),
            ["Chamber", "Ceiling-visible", "Shaft-light", "Distant-opening"],
        )

    def test_abyss_keywords_follow_internal_light_model(self):
        self.assertEqual(
            get_depth_keywords("ABYSS"),
            ["Sealed", "Compression-fractures", "Interior-pressure", "No-above"],
        )


class PromptRegressionTests(unittest.TestCase):
    def test_json_builder_contains_new_depth_phrases(self):
        content = (REPO_ROOT / "src/prompts/json_builder.md").read_text()
        self.assertIn(
            "Depth is light direction, not just position",
            content,
        )
        self.assertIn(
            'beneath [environment] formations, partial sky still visible, light entering from one direction',
            content,
        )
        self.assertIn(
            'inside subterranean [environment] chambers, vaulted ceiling visible above, light descending as a directed shaft from a distant opening',
            content,
        )
        self.assertIn(
            'sealed within the buried [environment] core, material pressing from all sides, illuminated only by thin blades through compression fractures',
            content,
        )
        self.assertNotIn("within enclosed [environment] [chambers/caverns]", content)
        self.assertNotIn(
            "at the primordial [environment] core, near-darkness pressing in from all sides",
            content,
        )
        self.assertNotIn("{moon_count} moons in upper third", content)
        self.assertNotIn("{moon_count} moons in sky", content)

    def test_video_prompt_contains_abyss_fracture_guidance(self):
        content = (REPO_ROOT / "src/prompts/video.md").read_text()
        self.assertIn("a crescent or point glimpsed through a fracture, or absent", content)
        self.assertIn(
            "If depth_level is ABYSS: scene must feel sealed and compressed.",
            content,
        )


if __name__ == "__main__":
    unittest.main()
