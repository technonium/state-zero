#!/usr/bin/env python3
"""Render lightweight State Zero portfolio variants.

The normal daily path composites from the original art/video.  The optional
fallback path accepts an already-composited Instagram card and extracts its
visible artwork before applying the portfolio composition.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from composite import (
    ARC_SIZE,
    ARC_STROKE,
    ARC_SUPERSAMPLE,
    ARCS,
    FONT_BOLD,
    FONT_MEDIUM,
    SPARK_ICON_PNG,
    resize_cover,
)

PORTFOLIO_W, PORTFOLIO_H = 1080, 1701
PORTFOLIO_VIDEO_W, PORTFOLIO_VIDEO_H = 720, 1134
PORTFOLIO_VIDEO_MAX_BYTES = 1_500_000

# Figma node 204:119 / 204:181. The exact opaque frame exports own the
# artwork aperture, including its slanted corner and bottom band.
ASSETS_DIR = Path(__file__).parent.parent / "assets"
PORTFOLIO_FRAME_LIGHT = ASSETS_DIR / "portfolio_frame_light.png"
PORTFOLIO_FRAME_DARK = ASSETS_DIR / "portfolio_frame_dark.png"
ART_X, ART_Y = 80, 364
ART_W, ART_BOTTOM_BAND = 920, 80
ART_H = PORTFOLIO_H - ART_Y - ART_BOTTOM_BAND
DATE_X, DATE_Y, DATE_W, DATE_H = 80, 80, 279, 71
ARC_Y = 80
SPARK_X, SPARK_Y, SPARK_SIZE = 929, 80, 71
TITLE_X, TITLE_Y, TITLE_SIZE = 80, 216, 100

# The visible art area of the existing 1080x1920 Instagram card.  This is
# intentionally used only for the prebuilt emergency fallback source.
FALLBACK_ART_X, FALLBACK_ART_Y = 0, 462
FALLBACK_ART_W, FALLBACK_ART_H = 1080, 1256


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    if not path.exists():
        raise FileNotFoundError(f"Required font is missing: {path}")
    return ImageFont.truetype(str(path), size)


def _theme(theme: str) -> tuple[str, tuple[int, int, int, int]]:
    if theme == "light":
        return "white", (0, 0, 0, 255)
    if theme == "dark":
        return "black", (255, 255, 255, 255)
    raise ValueError(f"Unknown portfolio theme: {theme}")


def _frame_path(theme: str) -> Path:
    _theme(theme)  # Validate before choosing an asset.
    return PORTFOLIO_FRAME_LIGHT if theme == "light" else PORTFOLIO_FRAME_DARK


def _load_frame(theme: str) -> Image.Image:
    path = _frame_path(theme)
    if not path.exists():
        raise FileNotFoundError(f"Required Figma portfolio frame is missing: {path}")
    frame = Image.open(path).convert("RGBA")
    if frame.size != (PORTFOLIO_W, PORTFOLIO_H):
        raise ValueError(f"Figma portfolio frame must be {PORTFOLIO_W}x{PORTFOLIO_H}, got {frame.size}: {path}")
    return frame


def _draw_arc(canvas: Image.Image, x: int, fill_pct: float, color: tuple[int, int, int, int]):
    ss = ARC_SUPERSAMPLE
    hi_size = ARC_SIZE * ss
    arc = Image.new("RGBA", (hi_size, hi_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(arc)
    track = (*color[:3], 38)
    bbox = [1, 1, hi_size - 2, hi_size - 2]
    draw.arc(bbox, 0, 360, fill=track, width=ARC_STROKE * ss)
    pct = max(0.0, min(1.0, float(fill_pct)))
    if pct:
        draw.arc(bbox, 270, 270 + 360 * pct, fill=color, width=ARC_STROKE * ss)
    canvas.alpha_composite(arc.resize((ARC_SIZE, ARC_SIZE), Image.LANCZOS), (x, ARC_Y))


def _draw_spark(canvas: Image.Image, color: tuple[int, int, int, int]):
    if not SPARK_ICON_PNG.exists():
        raise FileNotFoundError(f"Required spark icon is missing: {SPARK_ICON_PNG}")
    icon = Image.open(SPARK_ICON_PNG).convert("RGBA")
    if icon.size != (SPARK_SIZE, SPARK_SIZE):
        icon = icon.resize((SPARK_SIZE, SPARK_SIZE), Image.LANCZOS)
    alpha = icon.getchannel("A")
    tinted = Image.new("RGBA", icon.size, color)
    tinted.putalpha(alpha)
    canvas.alpha_composite(tinted, (SPARK_X, SPARK_Y))


def _draw_ui(theme: str, data: dict) -> Image.Image:
    _background, color = _theme(theme)
    # This is the exact Figma overlay. Its opaque pixels hide source media and
    # its transparent aperture reveals it, matching the original card-frame
    # compositing model rather than approximating the diagonal in drawing code.
    overlay = _load_frame(theme)
    draw = ImageDraw.Draw(overlay)
    color_rgb = color[:3]
    for offset in range(5):
        draw.rectangle(
            [DATE_X + offset, DATE_Y + offset, DATE_X + DATE_W - 1 - offset, DATE_Y + DATE_H - 1 - offset],
            outline=color_rgb,
        )

    date_font = _font(FONT_MEDIUM, 40)
    date_text = str(data.get("date", "")).upper()
    bbox = date_font.getbbox(date_text)
    tx = DATE_X + (DATE_W - (bbox[2] - bbox[0])) // 2
    ty = DATE_Y + (DATE_H - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((tx, ty), date_text, font=date_font, fill=color_rgb)

    for arc in ARCS:
        value = data.get(arc["key"], 0) or 0
        _draw_arc(overlay, arc["x"], float(value) / arc["max_val"], color)
    _draw_spark(overlay, color)

    title = str(data.get("title", "")).upper()
    title_font = _font(FONT_BOLD, TITLE_SIZE)
    width = title_font.getbbox(title)[2] - title_font.getbbox(title)[0]
    if width > ART_W:
        title_font = _font(FONT_BOLD, max(60, int(TITLE_SIZE * ART_W / width)))
    draw.text((TITLE_X, TITLE_Y), title, font=title_font, fill=color_rgb)
    return overlay


def _fallback_image_art(source: Image.Image) -> Image.Image:
    return source.crop((FALLBACK_ART_X, FALLBACK_ART_Y, FALLBACK_ART_X + FALLBACK_ART_W, FALLBACK_ART_Y + FALLBACK_ART_H))


def render_still(source_path: Path, output_path: Path, data: dict, theme: str, *, fallback_card: bool = False):
    background, _color = _theme(theme)
    with Image.open(source_path) as source:
        art_source = _fallback_image_art(source.convert("RGB")) if fallback_card else source.convert("RGB")
    art = resize_cover(art_source, ART_W, ART_H)
    canvas = Image.new("RGB", (PORTFOLIO_W, PORTFOLIO_H), background)
    # Deliberately let media extend below the Figma frame; the exact exported
    # frame performs the final crop and prevents edge seams in both themes.
    canvas.paste(art, (ART_X, ART_Y))
    overlay = _draw_ui(theme, data)
    canvas.paste(overlay, (0, 0), overlay)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "WEBP", quality=82, method=6)


def _video_filter(theme: str, fallback_card: bool) -> str:
    background, _color = _theme(theme)
    prefix = ""
    input_label = "0:v"
    if fallback_card:
        prefix = f"[0:v]crop={FALLBACK_ART_W}:{FALLBACK_ART_H}:{FALLBACK_ART_X}:{FALLBACK_ART_Y}[fallback];"
        input_label = "fallback"
    return (
        f"{prefix}[{input_label}]scale={ART_W}:{ART_H}:force_original_aspect_ratio=increase,"
        f"crop={ART_W}:{ART_H}[art];"
        f"color=c={background}:s={PORTFOLIO_W}x{PORTFOLIO_H}[base];"
        f"[base][art]overlay={ART_X}:{ART_Y}[card];"
        # Do not impose a delivery frame rate: the portfolio video keeps the
        # source cadence (the supplied fallback is 25 fps).
        f"[card][1:v]overlay=0:0,scale={PORTFOLIO_VIDEO_W}:{PORTFOLIO_VIDEO_H},format=yuv420p[v]"
    )


def render_video(source_path: Path, output_path: Path, data: dict, theme: str, *, fallback_card: bool = False):
    """Encode a compact portfolio MP4, retaining source audio when available."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    overlay = _draw_ui(theme, data)
    with tempfile.TemporaryDirectory() as tmpdir:
        overlay_path = Path(tmpdir) / "portfolio_overlay.png"
        overlay.save(overlay_path, "PNG")
        last_error = None
        for crf in (28, 30, 32):
            candidate = output_path.with_name(f".{output_path.stem}-crf{crf}.mp4")
            cmd = [
                "ffmpeg", "-y", "-i", str(source_path), "-loop", "1", "-i", str(overlay_path),
                "-filter_complex", _video_filter(theme, fallback_card),
                "-map", "[v]", "-map", "0:a?", "-shortest",
                "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                "-maxrate", "900k", "-bufsize", "1800k", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(candidate),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode:
                last_error = result.stderr[-2000:]
                candidate.unlink(missing_ok=True)
                continue
            if candidate.stat().st_size <= PORTFOLIO_VIDEO_MAX_BYTES:
                candidate.replace(output_path)
                return
            candidate.unlink(missing_ok=True)
        raise RuntimeError(f"Portfolio video could not meet {PORTFOLIO_VIDEO_MAX_BYTES} byte ceiling. {last_error or ''}")


def render_variants(image_path: Path, video_path: Path, output_dir: Path, data: dict, *, fallback_card: bool = False):
    for theme in ("light", "dark"):
        render_still(image_path, output_dir / f"{theme}.webp", data, theme, fallback_card=fallback_card)
        render_video(video_path, output_dir / f"{theme}.mp4", data, theme, fallback_card=fallback_card)


def _daily_data(data_path: Path, meta_path: Path) -> dict:
    daily = json.loads(data_path.read_text(encoding="utf-8"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return {
        "date": meta.get("date_display", ""),
        "title": meta.get("title", ""),
        "strain": daily.get("strain", 0),
        "recovery": daily.get("recovery_pct", 0),
        "sleep_score": daily.get("sleep_score_pct", 0),
    }


def main():
    parser = argparse.ArgumentParser(description="Render State Zero portfolio media variants")
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--video", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--meta", type=Path)
    parser.add_argument("--fallback-card", action="store_true")
    parser.add_argument("--date", help="Fallback display date")
    parser.add_argument("--title", help="Fallback title")
    args = parser.parse_args()
    if args.fallback_card:
        # The approved ERROR 404 fallback card deliberately shows three full
        # rings. It has no WHOOP snapshot, so preserve that visual state rather
        # than rendering empty metrics.
        data = {
            "date": args.date or "27 JUL 1987",
            "title": args.title or "ERROR 404",
            "strain": 21,
            "recovery": 100,
            "sleep_score": 100,
        }
    else:
        if not args.data or not args.meta:
            parser.error("--data and --meta are required for daily portfolio media")
        data = _daily_data(args.data, args.meta)
    render_variants(args.image, args.video, args.output_dir, data, fallback_card=args.fallback_card)


if __name__ == "__main__":
    main()
