"""Narrow adapter from State Zero's media files to the pinned gflow CLI."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image

from google_image_client import GoogleImageClient
from google_video_client import GoogleVideoClient


class FlowMediaClient:
    def __init__(self, profile: str | None = None):
        self.profile = profile or os.getenv("GFLOW_PROFILE", "shadow")

    def image_command(self, prompt_json: dict, raw_path: Path) -> list[str]:
        return [
            "gflow", "image", "t2i", GoogleImageClient._build_prompt_from_json(prompt_json),
            "--model", "nano2", "--aspect", "3:4", "--count", "1",
            "--output", str(raw_path), "--profile", self.profile, "--json",
        ]

    def video_command(self, prompt: str, start_frame: Path, raw_path: Path) -> list[str]:
        command = [
            "gflow", "video", "i2v", "--initial-frame", str(start_frame), prompt,
            "--model", "veo-fast", "--aspect", "9:16", "--count", "1",
        ]
        if os.getenv("FLOW_VIDEO_EXPLICIT_DURATION", "true").lower() == "true":
            command.extend(("--duration", "8"))
        command.extend(("--output", str(raw_path), "--profile", self.profile, "--json"))
        return command

    @staticmethod
    def _run_json(command: list[str], timeout: int) -> dict:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode:
            # CLI output can contain private prompts or signed URLs; leave its private
            # incident bundle for diagnosis instead of copying output into app logs.
            raise RuntimeError(f"gflow {command[1]} {command[2]} exited {result.returncode}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("gflow did not return one JSON result") from exc
        if payload.get("status") != "ok":
            raise RuntimeError("gflow reported an unsuccessful generation")
        return payload

    @staticmethod
    def _owned_path(raw: str, directory: Path) -> Path:
        path = Path(raw).resolve(strict=True)
        if not path.is_relative_to(directory.resolve(strict=True)) or not path.is_file():
            raise RuntimeError("gflow returned an output outside the run directory")
        return path

    @staticmethod
    def _write_diagnostics(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def generate_image(self, prompt_json: dict, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._run_json(self.image_command(prompt_json, output_path.with_name("flow_art.png")), 600)
        images = payload.get("images") or []
        if payload.get("count") != 1 or len(images) != 1:
            raise RuntimeError("Flow did not return exactly one image")
        item = images[0]
        if payload.get("model") != "nano2" or item.get("model_name_type") != "NARWHAL":
            raise RuntimeError("Flow did not attribute the image to Nano Banana 2")
        source = self._owned_path(item["local_path"], output_path.parent)
        original = source.name
        upscaled = False
        if os.getenv("FLOW_IMAGE_UPSCALE_2K", "true").lower() == "true":
            media_id, project_id = item.get("media_name"), payload.get("project_id")
            if media_id and project_id:
                upscale_dir = output_path.parent / "flow_upscale"
                upscale_dir.mkdir(exist_ok=True)
                command = [
                    "gflow", "image", "upscale", media_id, "--scale", "2k",
                    "--project", project_id, "--out", str(upscale_dir), "--profile", self.profile,
                ]
                try:
                    result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
                    matches = list(upscale_dir.rglob(f"{media_id}_2k.*")) if result.returncode == 0 else []
                    if len(matches) == 1:
                        source = self._owned_path(str(matches[0]), output_path.parent)
                        upscaled = True
                except subprocess.TimeoutExpired:
                    pass  # Keep the original image; the resolution gap stays visible in diagnostics.
        with Image.open(source) as image:
            image.load()
            width, height = image.size
            if width < 512 or height < 512 or abs(width / height - 0.75) > 0.015:
                raise RuntimeError(f"Flow image has invalid dimensions: {width}x{height}")
            image.convert("RGB").save(output_path, "PNG")
        self._write_diagnostics(output_path.with_name("flow_image_diagnostics.json"), {
            "model": "nano2", "wire_model": item["model_name_type"], "aspect": "3:4", "source_width": width,
            "source_height": height, "flow_2k_upscaled": upscaled,
            "media_id": item.get("media_name"), "project_id": payload.get("project_id"),
            "original_file": original, "source_file": str(source.relative_to(output_path.parent.resolve())),
        })
        return output_path

    @staticmethod
    def _probe_video(path: Path) -> tuple[int, int, float]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        return int(stream["width"]), int(stream["height"]), float(payload["format"]["duration"])

    def generate_video(self, prompt_text: str, image_path: Path, output_path: Path) -> Path:
        if not prompt_text.strip():
            raise ValueError("Video prompt is empty")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        start_frame = output_path.with_name("flow_start_frame.png")
        framed, _ = GoogleVideoClient._to_letterboxed_png_bytes(image_path)
        start_frame.write_bytes(framed)
        raw_path = output_path.with_name("flow_raw_video.mp4")
        payload = self._run_json(self.video_command(prompt_text, start_frame, raw_path), 1500)
        request = payload.get("request") or {}
        if (not payload.get("succeeded") or request.get("count") != 1 or
                request.get("model") != "veo_3_1_fast" or request.get("mode") != "i2v" or
                request.get("aspect") != "portrait"):
            raise RuntimeError("Flow did not return one successful video")
        source = self._owned_path(payload["local_path"], output_path.parent)
        width, height, duration = self._probe_video(source)
        if (width, height) not in ((720, 1280), (1080, 1920)) or not 7.5 <= duration <= 8.5:
            raise RuntimeError(f"Flow video has invalid size or duration: {width}x{height}, {duration:.2f}s")
        staged = output_path.with_name(".generated_video.staged.mp4")
        if (width, height) == (720, 1280):
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-vf",
                 "scale=1080:1920:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
                 "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags",
                 "+faststart", str(staged)],
                capture_output=True, timeout=600, check=True,
            )
        else:
            shutil.copyfile(source, staged)
        staged.replace(output_path)
        self._write_diagnostics(output_path.with_name("flow_video_diagnostics.json"), {
            "model": "veo-fast", "aspect": "9:16", "media_id": payload.get("media_id"),
            "source_width": width, "source_height": height, "duration_seconds": duration,
            "locally_scaled_to_1080p": width == 720, "start_frame": start_frame.name,
        })
        return output_path
