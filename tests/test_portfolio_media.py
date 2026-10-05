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
from composite import process_video_card
from pipeline import WHOOPPipeline
from portfolio_media import (
    PORTFOLIO_H,
    PORTFOLIO_VIDEO_H,
    PORTFOLIO_VIDEO_MAX_BYTES,
    PORTFOLIO_VIDEO_W,
    PORTFOLIO_W,
    render_variants,
    render_video,
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

    def _assert_video_background(self, path: Path, theme: str):
        expected = (252, 252, 252) if theme == "light" else (13, 13, 13)
        for timestamp in ("0.2", "1.0", "1.7"):
            decoded = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", timestamp, "-i", str(path),
                 "-vf", "scale=in_color_matrix=bt709:in_range=tv:out_range=pc:flags=accurate_rnd+full_chroma_int",
                 "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
                check=True, capture_output=True,
            )
            frame = Image.frombytes("RGB", (PORTFOLIO_VIDEO_W, PORTFOLIO_VIDEO_H), decoded.stdout)
            for point in ((20, 20), (PORTFOLIO_VIDEO_W - 20, 20),
                          (20, PORTFOLIO_VIDEO_H - 20),
                          (PORTFOLIO_VIDEO_W - 20, PORTFOLIO_VIDEO_H - 20)):
                with self.subTest(theme=theme, timestamp=timestamp, point=point):
                    self.assertEqual(frame.getpixel(point), expected)

    def test_normal_videos_preserve_frame_background_colors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source.mp4"
            self._make_video(source)
            tagged_source = root / "tagged-source.mp4"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-c", "copy", "-bsf:v",
                 "h264_metadata=video_full_range_flag=0:colour_primaries=1:transfer_characteristics=13:matrix_coefficients=1",
                 str(tagged_source)], check=True,
            )
            for theme in ("light", "dark"):
                output = root / f"{theme}.mp4"
                render_video(tagged_source, output, {"date": "05 OCT 2026", "title": "COLOR CHECK"}, theme)
                self._assert_video_background(output, theme)

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
                        # (434, 80) lands on an antialiased edge of a metric
                        # arc, not inside its stroke: the arc is drawn
                        # supersampled and LANCZOS-downsampled, so the true
                        # value here is ~690 even against pure black. The old
                        # 700 bound cleared it only because lossy WebP happened
                        # to ring upward. Once the frame paper moved off pure
                        # black the encoder settled at 681 and the bound broke
                        # without anything about the ink changing. 600 keeps the
                        # ink/paper distinction this asserts (ink 681 vs paper
                        # 39-42) while no longer cutting through a blend pixel.
                        self.assertGreater(sum(image.convert("RGB").getpixel((434, 80))), 600)
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
                        "stream=codec_name,codec_type,width,height,r_frame_rate,pix_fmt,color_range,color_space,color_transfer,color_primaries:format=duration", "-of", "json", str(mp4),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                payload = json.loads(probe.stdout)
                self.assertAlmostEqual(float(payload["format"]["duration"]), 2.0, places=1)
                streams = payload["streams"]
                video = next(stream for stream in streams if stream["codec_type"] == "video")
                audio = next(stream for stream in streams if stream["codec_type"] == "audio")
                self.assertEqual(video["codec_name"], "h264")
                self.assertEqual((video["width"], video["height"]), (PORTFOLIO_VIDEO_W, PORTFOLIO_VIDEO_H))
                self.assertEqual(video["r_frame_rate"], source_fps)
                self.assertEqual(video["pix_fmt"], "yuv420p")
                self.assertEqual(
                    tuple(video[k] for k in ("color_range", "color_space", "color_transfer", "color_primaries")),
                    ("tv", "bt709", "iec61966-2-1", "bt709"),
                )
                self.assertEqual(audio["codec_name"], "aac")
                self._assert_video_background(mp4, theme)
                frame_path = root / f"{theme}-frame.png"
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", "0.5", "-i", str(mp4),
                     "-vf", "scale=in_color_matrix=bt709:in_range=tv:out_range=pc:flags=accurate_rnd+full_chroma_int",
                     "-frames:v", "1", str(frame_path)],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                frame = Image.open(frame_path).convert("RGB")
                if theme == "dark":
                    self.assertEqual(frame.getpixel((0, 0)), (13, 13, 13))
                edge_pixel = frame.getpixel((667, 303))
                if theme == "light":
                    self.assertGreater(sum(edge_pixel), 700)
                else:
                    self.assertLess(sum(edge_pixel), 60)

    def test_full_card_video_has_explicit_srgb_color_tags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source, output = root / "source.mp4", root / "card.mp4"
            self._make_video(source)
            process_video_card(source, {"date": "28 SEP 2026", "title": "THRESHOLD", "description": "A short scene."}, output)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=color_range,color_space,color_transfer,color_primaries", "-of", "json", str(output)],
                capture_output=True, text=True, check=True,
            )
            video = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(
                tuple(video[k] for k in ("color_range", "color_space", "color_transfer", "color_primaries")),
                ("tv", "bt709", "iec61966-2-1", "bt709"),
            )

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
