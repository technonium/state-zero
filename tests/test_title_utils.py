import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from title_utils import (
    assess_title_candidate,
    build_structural_title_keys,
    structural_title_key,
)


class TitleUtilsTests(unittest.TestCase):
    def test_structural_title_key_matches_cross_shape(self):
        self.assertEqual(structural_title_key("ASH MERIDIAN"), "meridian")
        self.assertEqual(structural_title_key("MERIDIAN"), "meridian")
        self.assertEqual(structural_title_key("CLEAR REACH"), "reach")
        self.assertEqual(structural_title_key("REACH"), "reach")

    def test_build_structural_title_keys_preserves_unique_order(self):
        self.assertEqual(
            build_structural_title_keys(
                ["ASH MERIDIAN", "CLEAR REACH", "MERIDIAN", "OBSIDIAN REACH", "FROST LINE"]
            ),
            ["meridian", "reach", "line"],
        )

    def test_assess_title_candidate_rejects_exact_duplicate(self):
        assessment = assess_title_candidate(
            "ASH MERIDIAN",
            banned_exact_titles={"ash meridian"},
            banned_structural_keys={"meridian"},
        )

        self.assertIn("exact_recent_repeat", assessment.hard_rejection_reasons)

    def test_assess_title_candidate_rejects_short_one_word_title(self):
        assessment = assess_title_candidate(
            "TOR",
            banned_exact_titles=set(),
            banned_structural_keys=set(),
        )

        self.assertIn("one_word_too_short", assessment.hard_rejection_reasons)

    def test_assess_title_candidate_marks_structural_repeat_for_cross_shape(self):
        assessment = assess_title_candidate(
            "MERIDIAN",
            banned_exact_titles=set(),
            banned_structural_keys={"meridian"},
        )

        self.assertEqual(assessment.hard_rejection_reasons, [])
        self.assertEqual(assessment.soft_rejection_reasons, ["structural_recent_repeat"])


if __name__ == "__main__":
    unittest.main()
