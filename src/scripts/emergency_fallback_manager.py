import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from utils import env_bool, get_runtime_root


DEFAULT_FALLBACK_VERSION = "error_404_v1"
REQUIRED_MANIFEST_FIELDS = {
    "version",
    "title",
    "scene_description",
    "caption",
    "local_png_path",
    "local_mp4_path",
    "sha256_png",
    "sha256_mp4",
    "video_duration_seconds",
    "prehosted_video_url",
    "prehosted_thumb_url",
}


class FallbackUnavailableError(RuntimeError):
    """Raised when the emergency fallback cannot be used."""


class EmergencyFallbackManager:
    def __init__(self, version: str = DEFAULT_FALLBACK_VERSION):
        self.version = version
        self.runtime_root = get_runtime_root()
        self.fallback_root = self.runtime_root / "fallback" / version
        self.manifest_path = self.fallback_root / "manifest.json"
        self.manifest: dict | None = None
        self._resolved_png_path: Path | None = None
        self._resolved_mp4_path: Path | None = None

        if not env_bool("EMERGENCY_FALLBACK_ENABLED", default=False):
            raise FallbackUnavailableError("EMERGENCY_FALLBACK_ENABLED is disabled.")

    def load_and_validate_manifest(self) -> dict:
        if not self.manifest_path.exists():
            raise FallbackUnavailableError(f"Manifest not found: {self.manifest_path}")

        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise FallbackUnavailableError(f"Failed to load manifest JSON: {exc}") from exc

        missing_fields = sorted(REQUIRED_MANIFEST_FIELDS - set(manifest.keys()))
        if missing_fields:
            raise FallbackUnavailableError(
                f"Manifest is missing required fields: {', '.join(missing_fields)}"
            )

        if manifest.get("version") != self.version:
            raise FallbackUnavailableError(
                f"Manifest version mismatch: expected {self.version}, got {manifest.get('version')}"
            )

        self._resolved_png_path = self._resolve_runtime_relative_path(manifest["local_png_path"])
        self._resolved_mp4_path = self._resolve_runtime_relative_path(manifest["local_mp4_path"])

        for field_name in ("sha256_png", "sha256_mp4"):
            value = str(manifest.get(field_name) or "").strip().lower()
            if len(value) != 64:
                raise FallbackUnavailableError(f"Manifest field {field_name} must be a 64-char SHA256 hex digest.")

        try:
            duration = float(manifest["video_duration_seconds"])
        except Exception as exc:
            raise FallbackUnavailableError("Manifest video_duration_seconds must be a number.") from exc
        if duration <= 0:
            raise FallbackUnavailableError("Manifest video_duration_seconds must be greater than zero.")

        video_url = (manifest.get("prehosted_video_url") or "").strip()
        thumb_url = (manifest.get("prehosted_thumb_url") or "").strip()
        if not video_url or not thumb_url:
            raise FallbackUnavailableError("Manifest must include both prehosted_video_url and prehosted_thumb_url.")

        self.manifest = manifest
        return manifest

    def verify_integrity(self) -> dict:
        manifest = self._require_manifest()
        png_path = self._resolved_png_path
        mp4_path = self._resolved_mp4_path
        assert png_path is not None
        assert mp4_path is not None

        if not png_path.exists():
            raise FallbackUnavailableError(f"Fallback PNG not found: {png_path}")
        if not mp4_path.exists():
            raise FallbackUnavailableError(f"Fallback MP4 not found: {mp4_path}")

        if self._sha256_file(png_path) != manifest["sha256_png"].strip().lower():
            raise FallbackUnavailableError(f"SHA256 mismatch for fallback PNG: {png_path}")
        if self._sha256_file(mp4_path) != manifest["sha256_mp4"].strip().lower():
            raise FallbackUnavailableError(f"SHA256 mismatch for fallback MP4: {mp4_path}")

        self._verify_png(png_path)
        actual_duration = self._probe_video_duration(mp4_path)
        expected_duration = float(manifest["video_duration_seconds"])
        if abs(actual_duration - expected_duration) > 0.5:
            raise FallbackUnavailableError(
                f"Fallback MP4 duration mismatch: expected {expected_duration:.2f}s, got {actual_duration:.2f}s"
            )

        return {
            "png_path": png_path,
            "mp4_path": mp4_path,
            "video_duration_seconds": actual_duration,
        }

    def copy_to_run_output(self, run_output_dir: Path) -> dict:
        if self._resolved_png_path is None or self._resolved_mp4_path is None:
            self.verify_integrity()

        run_output_dir.mkdir(parents=True, exist_ok=True)
        png_target = run_output_dir / "card_final.png"
        mp4_target = run_output_dir / "card_final.mp4"
        assert self._resolved_png_path is not None
        assert self._resolved_mp4_path is not None
        shutil.copy2(self._resolved_png_path, png_target)
        shutil.copy2(self._resolved_mp4_path, mp4_target)
        self._verify_staged_file(png_target, "fallback PNG")
        self._verify_staged_file(mp4_target, "fallback MP4")
        return {
            "png_path": png_target,
            "mp4_path": mp4_target,
        }

    def get_publish_strategy(self) -> dict:
        manifest = self._require_manifest()
        video_url = (manifest.get("prehosted_video_url") or "").strip()
        thumb_url = (manifest.get("prehosted_thumb_url") or "").strip()
        if not video_url or not thumb_url:
            raise FallbackUnavailableError("Prehosted fallback URLs are required.")
        return {
            "mode": "prehosted",
            "video_url": video_url,
            "thumb_url": thumb_url,
        }

    def build_fallback_caption(self, run_date: str) -> str:
        manifest = self._require_manifest()
        title = (manifest.get("title") or "ERROR 404").strip()
        caption = (manifest.get("caption") or "").strip()
        if caption.startswith(title):
            caption = f"{title} · {run_date}{caption[len(title):]}"
        elif caption:
            caption = f"{title} · {run_date}\n\n{caption}"
        else:
            caption = (
                f"{title} · {run_date}\n\n"
                "State Zero hit a pipeline fault today, so the emergency fallback card posted instead. "
                "Regular generation resumes next run."
            )
        return f"{caption}\n\n#statezero #dailyart #generativeart"

    def write_emergency_log(
        self,
        output_dir: Path,
        *,
        trigger_stage: str,
        reason: str,
        publish_mode: str | None,
        video_url: str,
        thumb_url: str,
        instagram_post_id: str | None,
        instagram_permalink: str | None,
        reused_existing_post: bool = False,
        publish_status: str | None = None,
        publish_diagnostics: dict | str | None = None,
    ) -> Path:
        manifest = self._require_manifest()
        output_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_date": output_dir.name,
            "asset_source": "emergency_fallback",
            "fallback_version": manifest["version"],
            "fallback_trigger_stage": trigger_stage,
            "fallback_reason": reason,
            "publish_mode": publish_mode,
            "reused_existing_post": reused_existing_post,
            "title": manifest["title"],
            "scene_description": manifest["scene_description"],
            "instagram_post_id": instagram_post_id,
            "instagram_permalink": instagram_permalink,
            "video_path_or_url": video_url,
            "image_path_or_url": thumb_url,
            "local_png_path": manifest["local_png_path"],
            "local_mp4_path": manifest["local_mp4_path"],
            "prehosted_video_url": manifest["prehosted_video_url"],
            "prehosted_thumb_url": manifest["prehosted_thumb_url"],
        }
        if publish_status is not None:
            payload["publish_status"] = publish_status
        if publish_diagnostics is not None:
            payload["publish_diagnostics"] = publish_diagnostics
        log_path = output_dir / "emergency_fallback_used.json"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=log_path.parent,
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
                tmp_path = handle.name
            os.replace(tmp_path, log_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        return log_path

    def _require_manifest(self) -> dict:
        if self.manifest is None:
            return self.load_and_validate_manifest()
        return self.manifest

    def _resolve_runtime_relative_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            raise FallbackUnavailableError(
                f"Manifest path must be relative to runtime root, got absolute path: {raw_path}"
            )
        if ".." in path.parts:
            raise FallbackUnavailableError(
                f"Manifest path may not traverse outside runtime root: {raw_path}"
            )

        resolved_runtime_root = self.runtime_root.resolve()
        resolved_path = (self.runtime_root / path).resolve()
        try:
            resolved_path.relative_to(resolved_runtime_root)
        except ValueError as exc:
            raise FallbackUnavailableError(
                f"Manifest path escapes runtime root: {raw_path}"
            ) from exc
        return resolved_path

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def _verify_staged_file(self, path: Path, label: str):
        if not path.is_file():
            raise FallbackUnavailableError(f"Failed to stage {label}: missing output file {path}")
        if path.stat().st_size <= 0:
            raise FallbackUnavailableError(f"Failed to stage {label}: output file is empty {path}")

    def _verify_png(self, path: Path):
        try:
            with Image.open(path) as img:
                img.verify()
        except Exception as exc:
            raise FallbackUnavailableError(f"Fallback PNG is unreadable: {exc}") from exc

    def _probe_video_duration(self, path: Path) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise FallbackUnavailableError(f"ffprobe failed for fallback MP4: {result.stderr.strip()}")
        try:
            duration = float((result.stdout or "0").strip() or "0")
        except ValueError as exc:
            raise FallbackUnavailableError("Fallback MP4 duration could not be parsed.") from exc
        if duration <= 0:
            raise FallbackUnavailableError("Fallback MP4 duration must be greater than zero.")
        return duration
