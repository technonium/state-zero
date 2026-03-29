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

    def test_deep_keywords_follow_geological_model(self):
        self.assertEqual(
            get_depth_keywords("DEEP"),
            ["Buried-recess", "Overhead-mass", "Compressed-enclosure", "Filtered-light"],
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
            'inside a buried [environment] recess, overhead geological mass pressing close above, light entering laterally from a crack in the surrounding rock — no opening above',
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
        content = (REPO_ROOT / "src/prompts/video.md").read_text().lower()
        self.assertIn("omit celestial presence entirely, or render it as a faint embedded impression within solid compressed material", content)
        self.assertIn("no cave mouth", content)
        self.assertIn("no skylight", content)
        self.assertIn("no tunnel exit", content)
        self.assertIn("camera must not be aimed upward toward any bright zone", content)
        self.assertIn("light does not enter from any opening above", content)

    def test_json_builder_contains_abyss_opening_bans(self):
        content = (REPO_ROOT / "src/prompts/json_builder.md").read_text().lower()
        self.assertIn("dominant bright zone in the upper third of the frame", content)
        self.assertIn("the upper portion of the image is enclosed material, not a luminous aperture", content)
        self.assertIn("omit entirely, or render as a faint shape impression fully embedded within solid compressed material", content)
        self.assertIn("light in abyss does not arrive from any above-direction source; it emanates from within the compressed material itself", content)
        self.assertIn("abyss shows no cave mouth, skylight, tunnel exit, horizon, scenic opening, or dominant bright zone in the upper frame", content)

    def test_deep_has_no_overhead_aperture_language(self):
        jb = (REPO_ROOT / "src/prompts/json_builder.md").read_text()
        vid = (REPO_ROOT / "src/prompts/video.md").read_text()
        combined = jb + vid
        forbidden = [
            "natural gap in the rock",
            "shaft-light",
            "descending from a distant geological opening",
            "geological gap",
            "from one natural gap",
            "filtered light from geological gap",
            "filtered directional light from one natural gap",
        ]
        for phrase in forbidden:
            self.assertNotIn(
                phrase,
                combined,
                msg=f"Forbidden DEEP overhead-aperture phrase found: {phrase!r}",
            )


if __name__ == "__main__":
    unittest.main()
