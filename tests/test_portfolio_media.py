import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from emergency_fallback_manager import EmergencyFallbackManager
from pipeline import WHOOPPipeline
from portfolio_media import (
    PORTFOLIO_H,
    PORTFOLIO_VIDEO_H,
    PORTFOLIO_VIDEO_MAX_BYTES,
    PORTFOLIO_VIDEO_W,
    PORTFOLIO_W,
    render_variants,
)


class PortfolioMediaTests(unittest.TestCase):
    def _make_video(self, path: Path):
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def test_fallback_variants_are_small_and_keep_audio(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            image_path = root / "card.png"
            video_path = root / "card.mp4"
            output = root / "portfolio"
            Image.new("RGB", (1080, 1920), "#668899").save(image_path)
            self._make_video(video_path)
            source_probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "json", str(video_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            source_fps = json.loads(source_probe.stdout)["streams"][0]["r_frame_rate"]

            render_variants(
                image_path,
                video_path,
                output,
                {"date": "27 JUL 1987", "title": "ERROR 404", "strain": 21, "recovery": 100, "sleep_score": 100},
                fallback_card=True,
            )

            for theme in ("light", "dark"):
                with Image.open(output / f"{theme}.webp") as image:
                    self.assertEqual(image.size, (PORTFOLIO_W, PORTFOLIO_H))
                    self.assertEqual(image.format, "WEBP")
                    bottom_pixel = image.convert("RGB").getpixel((PORTFOLIO_W // 2, PORTFOLIO_H - 1))
                    if theme == "light":
                        self.assertGreater(sum(bottom_pixel), 700)
                        self.assertLess(sum(image.convert("RGB").getpixel((434, 80))), 200)
                        self.assertGreater(sum(image.convert("RGB").getpixel((999, 454))), 700)
                        self.assertGreater(sum(image.convert("RGB").getpixel((920, 378))), 700)
                    else:
                        self.assertLess(sum(bottom_pixel), 60)
                        self.assertGreater(sum(image.convert("RGB").getpixel((434, 80))), 700)
                        self.assertLess(sum(image.convert("RGB").getpixel((999, 454))), 60)
                        self.assertLess(sum(image.convert("RGB").getpixel((920, 378))), 60)
                mp4 = output / f"{theme}.mp4"
                self.assertLessEqual(mp4.stat().st_size, PORTFOLIO_VIDEO_MAX_BYTES)
                # `+faststart` puts metadata ahead of the media payload so a
                # browser can begin playback without downloading the file.
                mp4_bytes = mp4.read_bytes()
                self.assertLess(mp4_bytes.find(b"moov"), mp4_bytes.find(b"mdat"))
                probe = subprocess.run(
                    [
                        "ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_name,codec_type,width,height,r_frame_rate,pix_fmt", "-of", "json", str(mp4),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                streams = json.loads(probe.stdout)["streams"]
                video = next(stream for stream in streams if stream["codec_type"] == "video")
                audio = next(stream for stream in streams if stream["codec_type"] == "audio")
                self.assertEqual(video["codec_name"], "h264")
                self.assertEqual((video["width"], video["height"]), (PORTFOLIO_VIDEO_W, PORTFOLIO_VIDEO_H))
                self.assertEqual(video["r_frame_rate"], source_fps)
                self.assertEqual(video["pix_fmt"], "yuv420p")
                self.assertEqual(audio["codec_name"], "aac")
                frame_path = root / f"{theme}-frame.png"
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", "0.5", "-i", str(mp4), "-frames:v", "1", str(frame_path)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                frame = Image.open(frame_path).convert("RGB")
                edge_pixel = frame.getpixel((667, 303))
                if theme == "light":
                    self.assertGreater(sum(edge_pixel), 700)
                else:
                    self.assertLess(sum(edge_pixel), 60)

    def test_fallback_sidecars_copy_to_date_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            private_root = Path(tmpdir) / "private"
            source = private_root / "runtime" / "fallback" / "error_404_v1" / "portfolio"
            source.mkdir(parents=True)
            for theme, color in (("light", "white"), ("dark", "black")):
                Image.new("RGB", (1080, 1701), color).save(source / f"{theme}.webp")
                self._make_video(source / f"{theme}.mp4")
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": str(private_root), "EMERGENCY_FALLBACK_ENABLED": "true"}, clear=False):
                manager = EmergencyFallbackManager()
                destination = manager.copy_portfolio_to_run_output(private_root / "runtime" / "output" / "2026-07-24")
            self.assertIsNotNone(destination)
            self.assertGreater((destination / "dark.mp4").stat().st_size, 0)

    def test_corrupt_fallback_sidecar_is_rejected_without_affecting_fallback_post(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            private_root = Path(tmpdir) / "private"
            source = private_root / "runtime" / "fallback" / "error_404_v1" / "portfolio"
            source.mkdir(parents=True)
            for theme, color in (("light", "white"), ("dark", "black")):
                Image.new("RGB", (1080, 1701), color).save(source / f"{theme}.webp")
                self._make_video(source / f"{theme}.mp4")
            (source / "dark.webp").write_bytes(b"not a webp")
            with patch.dict(os.environ, {"STATE_ZERO_PRIVATE_ROOT": str(private_root), "EMERGENCY_FALLBACK_ENABLED": "true"}, clear=False):
                manager = EmergencyFallbackManager()
                with self.assertRaisesRegex(RuntimeError, "unreadable"):
                    manager.copy_portfolio_to_run_output(private_root / "runtime" / "output" / "2026-07-24")

    def test_portfolio_uploader_writes_date_archive_and_latest_alias(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            portfolio = root / "portfolio"
            portfolio.mkdir()
            for name in ("light.webp", "dark.webp", "light.mp4", "dark.mp4"):
                (portfolio / name).write_bytes(name.encode())
            pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
            pipeline.run_date = "2026-07-24"
            pipeline.post_to_instagram = True
            pipeline.media_mode = "local_test"
            pipeline.local_vps_dir = root / "served"
            with patch.dict(os.environ, {"VPS_PUBLIC_BASE_URL": "https://media.example.test"}, clear=False):
                with patch.object(pipeline, "_ensure_public_urls_reachable", return_value=[]):
                    urls = pipeline.step_17_upload_portfolio_vps(portfolio)
            self.assertEqual((root / "served" / "portfolio" / "2026-07-24" / "light.webp").read_bytes(), b"light.webp")
            self.assertEqual((root / "served" / "portfolio" / "latest" / "dark.mp4").read_bytes(), b"dark.mp4")
            self.assertEqual(urls["light.webp"], "https://media.example.test/portfolio/latest/light.webp")

    def test_disabled_secondary_is_a_noop(self):
        pipeline = WHOOPPipeline.__new__(WHOOPPipeline)
        pipeline.portfolio_media_enabled = False
        pipeline._run_portfolio_media_secondary()


if __name__ == "__main__":
    unittest.main()
