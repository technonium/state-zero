#!/usr/bin/env python3
"""
State Zero — Compositing Script
=========================================
Takes an AI-generated media file + pipeline data and produces
a final 1080×1920 card matching the Figma template pixel-for-pixel.

Usage:
    python composite.py --test --type image
    python composite.py --image art.png --type image --data daily.json --meta meta.json --output card.png
    python composite.py --video art.mp4 --type video --data daily.json --meta meta.json --output card.mp4
    python composite.py --image art.png --data daily.json --metadata meta.json --output card.png
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ═══════════════════════════════════════════════════════
#  LAYOUT CONSTANTS — extracted from Figma node 900:1081
# ═══════════════════════════════════════════════════════

CARD_W, CARD_H = 1080, 1920

# White borders
BORDER_L = 80
BORDER_R = 80

# Content area
CONTENT_X = 80
CONTENT_W = 920  # 1080 − 80 − 80

# Header zone
HEADER_H = 462

# Media placement
IMAGE_ART_OFFSET_Y = 367
VIDEO_ART_OFFSET_Y = 130

# Footer zone
FOOTER_Y = 1718
FOOTER_H = 202

# ── Date box ──────────────────────────────────
DATE_X, DATE_Y = 80, 178
DATE_W, DATE_H = 279, 71
DATE_BORDER = 5

# ── Metric arcs ───────────────────────────────
ARC_Y = 178
ARC_SIZE = 71                # 71×71px circles
ARC_STROKE = 5               # 5px stroke width, matches date box border
ARC_TRACK_COLOR = (0, 0, 0, 26)    # 10% opacity black track (26/255 ≈ 0.10)
ARC_FILL_COLOR = (0, 0, 0, 255)    # 100% black filled arc
ARC_SUPERSAMPLE = 8          # render at 8x for crisp anti-aliased arcs
ARCS = [
    {"x": 399, "key": "sleep_score", "max_val": 100.0},   # 0–100%
    {"x": 510, "key": "recovery",    "max_val": 100.0},   # 0–100%
    {"x": 621, "key": "strain",      "max_val": 21.0},    # 0–21 scale
]

# ── Spark icon ────────────────────────────────
SPARK_X, SPARK_Y = 929, 178
SPARK_SIZE = 71

# ── Card title ────────────────────────────────
TITLE_X, TITLE_Y = 80, 314
TITLE_FONT_SIZE = 100

# ── Description ───────────────────────────────
DESC_X, DESC_Y = 80, 1768
DESC_W = 920
DESC_FONT_SIZE = 40

# ── Asset locations ───────────────────────────
ASSETS_DIR = Path(__file__).parent.parent / "assets"
FONT_BOLD = ASSETS_DIR / "SpaceGrotesk-Bold.ttf"
FONT_MEDIUM = ASSETS_DIR / "SpaceGrotesk-Medium.ttf"
CARD_FRAME_PNG = ASSETS_DIR / "card_frame.png"
SPARK_ICON_PNG = ASSETS_DIR / "spark_icon.png"

# ── Test media sizes ──────────────────────────
TEST_IMAGE_W = CARD_W
TEST_IMAGE_H = 1440
TEST_VIDEO_W = CARD_W
TEST_VIDEO_H = CARD_H


# ═══════════════════════════════════════════════════════
#  FONT LOADING
# ═══════════════════════════════════════════════════════

def load_fonts() -> dict[str, ImageFont.FreeTypeFont]:
    """Load Space Grotesk Bold (title) and Medium (date, description)."""
    if not FONT_BOLD.exists():
        raise FileNotFoundError(f"Font not found: {FONT_BOLD}")
    if not FONT_MEDIUM.exists():
        raise FileNotFoundError(f"Font not found: {FONT_MEDIUM}")
    return {
        "title": ImageFont.truetype(str(FONT_BOLD), TITLE_FONT_SIZE),
        "text":  ImageFont.truetype(str(FONT_MEDIUM), DESC_FONT_SIZE),
    }


# ═══════════════════════════════════════════════════════
#  IMAGE HELPERS
# ═══════════════════════════════════════════════════════

def resize_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize image to cover target size, then center-crop."""
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = int(src_w * scale), int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def make_test_art(w: int, h: int) -> Image.Image:
    """Generate a placeholder gradient for testing."""
    img = Image.new("RGBA", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(20 + 40 * t)
        g = int(80 + 60 * (1 - t))
        b = int(60 + 100 * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))
    return img


def make_test_video(output_path: Path, w: int, h: int):
    """Generate a silent 2-second blue/purple scrolling MP4."""
    try:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c=blue:s={w}x{h}:d=2",
            "-c:v", "libx264", str(output_path)
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"Error creating test video (is ffmpeg installed?): {e}")
        sys.exit(1)


def check_ffmpeg():
    """Verify ffmpeg is installed."""
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    except Exception:
        print("ERROR: ffmpeg is not installed or not in PATH. Required for video compositing.")
        print("Run: brew install ffmpeg")
        sys.exit(1)


# ═══════════════════════════════════════════════════════
#  DRAWING PRIMITIVES
# ═══════════════════════════════════════════════════════

def draw_bordered_rect(
    draw: ImageDraw.ImageDraw,
    x: int, y: int, w: int, h: int,
    border: int = 5,
    fill_color: str | None = "white",
    border_color: str = "black",
) -> None:
    """Draw a rectangle with a thick border."""
    if fill_color:
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=fill_color)
    for i in range(border):
        draw.rectangle(
            [x + i, y + i, x + w - 1 - i, y + h - 1 - i],
            outline=border_color,
        )


def draw_arc_indicator(
    canvas: Image.Image,
    x: int, y: int,
    size: int,
    fill_pct: float,
    stroke: int = ARC_STROKE,
) -> None:
    """Draw a circular arc progress indicator."""
    ss = ARC_SUPERSAMPLE
    hi_size = size * ss
    hi_stroke = stroke * ss

    arc_hi = Image.new("RGBA", (hi_size, hi_size), (0, 0, 0, 0))
    arc_draw = ImageDraw.Draw(arc_hi)

    margin = 1
    bbox = [margin, margin, hi_size - 1 - margin, hi_size - 1 - margin]

    arc_draw.arc(bbox, 0, 360, fill=ARC_TRACK_COLOR, width=hi_stroke)

    pct = max(0.0, min(1.0, fill_pct))
    if pct > 0:
        start_angle = 270
        end_angle = 270 + 360 * pct
        arc_draw.arc(bbox, start_angle, end_angle, fill=ARC_FILL_COLOR, width=hi_stroke)

    arc_lo = arc_hi.resize((size, size), Image.LANCZOS)
    canvas.alpha_composite(arc_lo, dest=(x, y))


def draw_spark_icon(canvas: Image.Image, x: int, y: int, size: int) -> None:
    """Draw the spark/star decorative icon."""
    if SPARK_ICON_PNG.exists():
        icon = Image.open(SPARK_ICON_PNG).convert("RGBA")
        if icon.size != (size, size):
            icon = icon.resize((size, size), Image.LANCZOS)
        canvas.paste(icon, (x, y), icon)
        return

    # Fallback SVG path
    draw = ImageDraw.Draw(canvas)
    half = size / 2
    scale = size / 35.5
    steps = 20
    for rotation in range(4):
        pts = []
        angle = rotation * (math.pi / 2)
        cos_a, sin_a = math.cos(angle), math.sin(angle)

        def rotate_pt(px, py):
            rx = (px - half) * cos_a - (py - half) * sin_a + half
            ry = (px - half) * sin_a + (py - half) * cos_a + half
            return (x + rx, y + ry)

        pts.append(rotate_pt(0, 0))
        pts.append(rotate_pt(0, 20.8013 * scale))
        for i in range(1, steps + 1):
            t = i / steps
            bx = (20.8013 * scale) + t * (half - 20.8013 * scale)
            by = (20.8013 * scale) + t * (half - 20.8013 * scale)
            curve = math.sin(t * math.pi) * 3 * scale
            pts.append(rotate_pt(bx + curve, by + curve))
        pts.append(rotate_pt(20.8013 * scale, 0))
        draw.polygon(pts, fill="black")


def draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: int, y: int,
    max_w: int,
    font: ImageFont.FreeTypeFont,
    fill: str = "black",
    line_height: int = 51,
    max_lines: int = 2,
) -> None:
    """Draw text with word-wrapping."""
    words = text.split()
    lines: list[str] = []
    current_line = ""

    for word in words:
        test = f"{current_line} {word}".strip()
        bbox = font.getbbox(test)
        text_w = bbox[2] - bbox[0]
        if text_w <= max_w:
            current_line = test
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    if len(lines) > max_lines:
        last = lines[max_lines - 1]
        remaining_words = last.split()
        while remaining_words:
            candidate = " ".join(remaining_words) + "…"
            if font.getbbox(candidate)[2] - font.getbbox(candidate)[0] <= max_w:
                break
            remaining_words.pop()
        lines = lines[:max_lines - 1] + [" ".join(remaining_words) + "…" if remaining_words else "…"]

    cy = y
    for line in lines:
        draw.text((x, cy), line, fill=fill, font=font)
        cy += line_height


# ═══════════════════════════════════════════════════════
#  UI OVERLAY GENERATOR
# ═══════════════════════════════════════════════════════

def build_ui_overlay(data: dict) -> Image.Image:
    """
    Generate a 1080x1920 transparent PNG containing only the UI elements:
    Header, Footer, Frame Border, Text, and Metric Arcs.
    """
    fonts = load_fonts()
    canvas = Image.new("RGBA", (CARD_W, CARD_H), (0, 0, 0, 0))

    # Optional: Fill header/footer/borders with solid white to block art behind it.
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, BORDER_L - 1, CARD_H - 1], fill="white")  # Left border
    draw.rectangle([CARD_W - BORDER_R, 0, CARD_W - 1, CARD_H - 1], fill="white")  # Right border
    draw.rectangle([BORDER_L, 0, CARD_W - BORDER_R - 1, HEADER_H - 1], fill="white")  # Header
    draw.rectangle([BORDER_L, FOOTER_Y, CARD_W - BORDER_R - 1, CARD_H - 1], fill="white")  # Footer

    # Frame overlay
    if CARD_FRAME_PNG.exists():
        frame = Image.open(CARD_FRAME_PNG).convert("RGBA")
        if frame.size != (CARD_W, CARD_H):
            frame = frame.resize((CARD_W, CARD_H), Image.LANCZOS)
        canvas = Image.alpha_composite(canvas, frame)

    draw = ImageDraw.Draw(canvas)

    # Date box
    draw_bordered_rect(draw, DATE_X, DATE_Y, DATE_W, DATE_H, border=DATE_BORDER)
    date_str = data["date"].upper()
    date_bbox = fonts["text"].getbbox(date_str)
    date_tw = date_bbox[2] - date_bbox[0]
    date_th = date_bbox[3] - date_bbox[1]
    date_tx = DATE_X + (DATE_W - date_tw) // 2
    date_ty = DATE_Y + (DATE_H - date_th) // 2 - date_bbox[1]
    draw.text((date_tx, date_ty), date_str, fill="black", font=fonts["text"])

    # Metric arcs
    for arc in ARCS:
        raw = data.get(arc["key"], 0)
        pct = raw / arc["max_val"]
        draw_arc_indicator(canvas, arc["x"], ARC_Y, ARC_SIZE, pct)
        draw = ImageDraw.Draw(canvas)

    # Spark icon
    draw_spark_icon(canvas, SPARK_X, SPARK_Y, SPARK_SIZE)
    draw = ImageDraw.Draw(canvas)

    # Title
    MIN_TITLE_SIZE = 60
    title_str = data["title"].upper()
    title_font = fonts["title"]
    title_bbox = title_font.getbbox(title_str)
    title_tw = title_bbox[2] - title_bbox[0]

    if title_tw > CONTENT_W:
        shrink_size = max(MIN_TITLE_SIZE, int(TITLE_FONT_SIZE * CONTENT_W / title_tw))
        title_font = ImageFont.truetype(str(FONT_BOLD), shrink_size)

    draw.text((TITLE_X, TITLE_Y), title_str, fill="black", font=title_font)

    # Description
    draw_wrapped_text(
        draw, data["description"], DESC_X, DESC_Y, DESC_W, fonts["text"]
    )

    return canvas


# ═══════════════════════════════════════════════════════
#  COMPOSITING PIPELINES
# ═══════════════════════════════════════════════════════

def process_image_card(art_path: Path, data: dict, output_path: Path):
    """
    Format A: 3:4 Image Layout
    Artwork is mapped to CARD_W width (ideally 1080×1446) and placed at
    IMAGE_ART_OFFSET_Y. Canvas auto-crops any overflow beyond CARD_H total height.
    """
    print(f"🖼️  Processing IMAGE layout (3:4 at Y={IMAGE_ART_OFFSET_Y})")
    
    # Base white canvas
    canvas = Image.new("RGB", (CARD_W, CARD_H), "white")
    
    # Load and resize art to width=1080 (maintaining its native ratio, ideally 1440h)
    art = Image.open(art_path).convert("RGB")
    aspect_ratio = art.width / art.height
    new_h = int(CARD_W / aspect_ratio)
    art = art.resize((CARD_W, new_h), Image.LANCZOS)
    
    # Paste exactly at IMAGE_ART_OFFSET_Y based on the Figma spec.
    # This automatically crops off any art that overflows CARD_H.
    canvas.paste(art, (0, IMAGE_ART_OFFSET_Y))

    # Generate and apply UI overlay
    ui_overlay = build_ui_overlay(data)
    canvas.paste(ui_overlay, (0, 0), ui_overlay)

    canvas.save(output_path, "PNG")
    print(f"✅ Saved image card: {output_path}")


def process_video_card(video_path: Path, data: dict, output_path: Path):
    """
    Format B: 16:9 Video Layout
    VEO3 vertical video is mapped to exactly CARD_W x CARD_H at
    VIDEO_ART_OFFSET_Y using FFmpeg.
    """
    check_ffmpeg()
    print(f"🎬 Processing VIDEO layout (FFmpeg compositing at Y={VIDEO_ART_OFFSET_Y})")

    # 1. Create the transparent UI overlay
    ui_overlay = build_ui_overlay(data)
    overlay_path = output_path.with_suffix(".overlay.png")
    ui_overlay.save(overlay_path, "PNG")

    # 2. Run FFmpeg to pad/offset the video and stamp the UI over it
    # We take the input video, scale it to CARD_W (maintaining its ratio),
    # pad the canvas to CARD_W x CARD_H (white background), place the video at
    # VIDEO_ART_OFFSET_Y,
    # and then overlay the transparent PNG UI on top of everything.
    # Encoding: ultrafast preset + CRF 28 for 5-10x speedup on VPS (2 vCPU)
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(overlay_path),
            "-filter_complex",
            # Scale video width to CARD_W, format to standard pixel format.
            f"[0:v]scale={CARD_W}:-1,format=yuv420p[scaled];"
            # Create a CARD_W x CARD_H white background.
            f"color=c=white:s={CARD_W}x{CARD_H}[bg];"
            # Overlay the video at VIDEO_ART_OFFSET_Y and end when the video ends.
            f"[bg][scaled]overlay=0:{VIDEO_ART_OFFSET_Y}:shortest=1[bg_vid];"
            # Overlay the UI PNG on top of the whole canvas (PNG repeats for video duration)
            "[bg_vid][1:v]overlay=0:0",
            "-c:v", "libx264",
            "-profile:v", "high",
            "-level", "4.1",
            "-preset", "medium",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-colorspace", "bt709",
            "-color_trc", "bt709",
            "-color_primaries", "bt709",
            "-threads", "4",
            # Copy original audio if it exists
            "-c:a", "copy",
            str(output_path)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        print(f"✅ Saved video card: {output_path}")
    except subprocess.CalledProcessError as e:
        print(f"FFmpeg Error: {e.stderr.decode()}")
        sys.exit(1)
    finally:
        # Cleanup temporary PNG
        overlay_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════
#  TEST DATA GENERATOR
# ═══════════════════════════════════════════════════════

def get_test_data() -> dict:
    return {
        "date": "25 FEB 2026",
        "title": "ASH MERIDIAN",
        "description": "The last recorded surface before the drift consumed the lower plains",
        "strain": 15.4,
        "recovery": 83,
        "sleep_score": 84,
    }

def run_test_image():
    art_path = Path("_test_image.png")
    out_path = Path("test_out_image.png")
    make_test_art(TEST_IMAGE_W, TEST_IMAGE_H).save(art_path)
    process_image_card(art_path, get_test_data(), out_path)
    art_path.unlink()

def run_test_video():
    vid_path = Path("_test_video.mp4")
    out_path = Path("test_out_video.mp4")
    make_test_video(vid_path, TEST_VIDEO_W, TEST_VIDEO_H)
    process_video_card(vid_path, get_test_data(), out_path)
    vid_path.unlink()


# ═══════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="State Zero Compositing (Dual Format)")
    parser.add_argument("--test", action="store_true", help="Run with sample data and generated media")
    parser.add_argument("--type", choices=["image", "video"], help="Outputs Format A (Image) or Format B (Video)")
    
    # Inputs for production
    parser.add_argument("--image", type=str, help="Path to input PNG/JPG (if --type image)")
    parser.add_argument("--video", type=str, help="Path to input MP4 (if --type video)")
    parser.add_argument("--data", type=str, help="Path to daily_data.json")
    parser.add_argument("--meta", type=str, help="Path to card_metadata.json")
    parser.add_argument("--metadata", type=str, help="Alias for --meta (legacy compatibility)")
    
    parser.add_argument("--output", type=str, default="final_output", help="Output path (extension is auto-added based on type)")
    args = parser.parse_args()

    if args.meta and args.metadata:
        parser.error("Use either --meta or --metadata, not both.")

    meta_path = args.meta or args.metadata

    render_type = args.type
    if not render_type:
        if args.video and not args.image:
            render_type = "video"
        elif args.image and not args.video:
            render_type = "image"
        else:
            parser.error("Specify --type, or provide exactly one of --image/--video.")

    # 1. TEST MODE
    if args.test:
        if render_type == "image":
            run_test_image()
        else:
            run_test_video()
        return

    # 2. PROD MODE
    if not args.data or not meta_path:
        parser.error("--data and --meta/--metadata JSON files are required in production mode.")

    # Parse JSONs
    with open(args.data) as f:
        daily_data = json.load(f)
    with open(meta_path) as f:
        card_meta = json.load(f)

    # Merge into the dictionary the drawing functions expect
    merged_data = {
        "date": card_meta.get("date_display", ""),
        "title": card_meta.get("title", ""),
        "description": card_meta.get("scene_description", ""),
        "strain": daily_data.get("strain", 0),
        "recovery": daily_data.get("recovery_pct", 0),
        "sleep_score": daily_data.get("sleep_score_pct", 0),
    }

    # Execute corresponding pipeline
    out_path = Path(args.output)
    if render_type == "image":
        if not args.image:
            parser.error("--image is required when --type=image")
        if out_path.suffix.lower() != ".png":
            out_path = out_path.with_suffix(".png")
        process_image_card(Path(args.image), merged_data, out_path)
    elif render_type == "video":
        if not args.video:
            parser.error("--video is required when --type=video")
        if out_path.suffix.lower() != ".mp4":
            out_path = out_path.with_suffix(".mp4")
        process_video_card(Path(args.video), merged_data, out_path)


if __name__ == "__main__":
    main()
