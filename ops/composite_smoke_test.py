#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from composite import CARD_H, CARD_W, process_image_card, process_video_card


def make_test_art(width: int, height: int) -> Image.Image:
    img = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / height
        red = int(20 + 40 * ratio)
        green = int(80 + 60 * (1 - ratio))
        blue = int(60 + 100 * ratio)
        draw.line([(0, y), (width, y)], fill=(red, green, blue, 255))
    return img


def make_test_video(output_path: Path, width: int, height: int):
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=blue:s={width}x{height}:d=2",
                "-c:v",
                "libx264",
                str(output_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as error:
        print(f"Error creating smoke-test video (is ffmpeg installed?): {error}")
        raise SystemExit(1) from error


def get_test_data() -> dict:
    return {
        "date": "25 FEB 2026",
        "title": "ASH MERIDIAN",
        "description": "The last recorded surface before the drift consumed the lower plains",
        "strain": 15.4,
        "recovery": 83,
        "sleep_score": 84,
    }


def run_image_smoke(output_dir: Path) -> Path:
    output_path = output_dir / "test_out_image.png"
    with tempfile.TemporaryDirectory() as tmpdir:
        art_path = Path(tmpdir) / "smoke_image.png"
        make_test_art(CARD_W, 1440).save(art_path)
        process_image_card(art_path, get_test_data(), output_path)
    return output_path


def run_video_smoke(output_dir: Path) -> Path:
    output_path = output_dir / "test_out_video.mp4"
    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = Path(tmpdir) / "smoke_video.mp4"
        make_test_video(video_path, CARD_W, CARD_H)
        process_video_card(video_path, get_test_data(), output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="State Zero composite smoke test")
    parser.add_argument(
        "--type",
        choices=["image", "video", "both"],
        default="both",
        help="Choose which output path to smoke test.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory for ignored smoke outputs such as test_out_image.png.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.type in {"image", "both"}:
        output_path = run_image_smoke(output_dir)
        print(f"Composite smoke image written to {output_path}")

    if args.type in {"video", "both"}:
        output_path = run_video_smoke(output_dir)
        print(f"Composite smoke video written to {output_path}")


if __name__ == "__main__":
    main()
