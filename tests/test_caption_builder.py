import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from caption_builder import build_caption


class CaptionBuilderTests(unittest.TestCase):
    def test_build_caption_uses_canonical_whoop_tag(self):
        caption = build_caption(
            {"title": "FROSTFISSURE", "date_display": "16 MAY 2026"},
            {"date": "2026-05-16", "date_display": "16 MAY 2026"},
        )

        self.assertIn("My daily @whoop data (sleep, recovery, yesterday's strain)", caption)
        self.assertNotIn("My daily WHOOP data, sleep", caption)

    def test_build_caption_cli_writes_canonical_caption(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data_path = tmp / "daily_data.json"
            meta_path = tmp / "card_metadata.json"
            output_path = tmp / "caption.txt"

            data_path.write_text(
                json.dumps({"date": "2026-05-16", "date_display": "16 MAY 2026"}),
                encoding="utf-8",
            )
            meta_path.write_text(
                json.dumps({"title": "FROSTFISSURE", "date_display": "16 MAY 2026"}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_ROOT / "build_caption.py"),
                    "--data",
                    str(data_path),
                    "--meta",
                    str(meta_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            self.assertIn("Caption written:", result.stdout)
            caption = output_path.read_text(encoding="utf-8")
            self.assertIn("FROSTFISSURE · 16 MAY 2026", caption)
            self.assertIn("My daily @whoop data (sleep, recovery, yesterday's strain)", caption)


if __name__ == "__main__":
    unittest.main()
