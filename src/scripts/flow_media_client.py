"""Narrow adapter from State Zero's media files to the pinned gflow CLI."""

import json
import fcntl
import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from google_image_client import GoogleImageClient
from google_video_client import GoogleVideoClient


class FlowCommandError(RuntimeError):
    def __init__(self, stage: str, category: str, *, error_class: str = "", media_id: str = "", retryable: bool = False, detail: str = "", operation_id: str = "", submission_state: str = ""):
        self.detail = detail
        self.operation_id = operation_id
        self.submission_state = submission_state
        self.category = category
        self.error_class = error_class
        self.media_id = media_id
        self.retryable = retryable
        super().__init__(f"Flow {stage} {category} ({error_class or 'unclassified'})")


class FlowMediaClient:
    def __init__(self, profile: str | None = None):
        self.profile = profile or os.getenv("GFLOW_PROFILE", "shadow")

    @staticmethod
    def _api_fallback_allowed(error: Exception) -> bool:
        if not isinstance(error, FlowCommandError):
            return False
        reason = (error.error_class + " " + error.detail).upper()
        if any(word in reason for word in ("SAFETY", "POLICY", "REJECT", "REFUSAL", "QUOTA", "CREDIT", "RESTRICT", "MODEL", "SETTING", "RECAPTCHA")):
            return False
        if error.category == "generation_failed":
            return error.retryable
        return (error.submission_state in ("", "not_attempted") and
                error.category in {"pre_submit_transient", "pre_submit_timeout", "auth_required"})

    def generate_image(self, prompt_json: dict, output_path: Path) -> Path:
        return self._generate_guarded("image", prompt_json, output_path,
                                      lambda result: self._generate_image(prompt_json, output_path, result))

    def generate_video(self, prompt_text: str, image_path: Path, output_path: Path) -> Path:
        request = {"prompt": prompt_text, "start_frame_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest()}
        return self._generate_guarded("video", request, output_path,
                                      lambda result: self._generate_video(prompt_text, image_path, output_path, result), image_path)

    def _generate_guarded(self, stage: str, request: dict, output: Path, generate, image_path: Path | None = None) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        diagnostics = output.with_name(f"flow_{stage}_diagnostics.json")
        identity = {"request": request, "profile": self.profile,
                    "project": os.getenv("FLOW_PREFLIGHT_PROJECT_ID", ""),
                    "settings": "Nano Banana 2.1/3:4/x1" if stage == "image" else "veo-fast/i2v/9:16/x1/8s"}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        with output.with_name(".flow-generation.lock").open("a") as lock:
            os.chmod(lock.name, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise FlowCommandError(stage, "execution_busy") from None
            previous = json.loads(diagnostics.read_text()) if diagnostics.exists() else {}
            markers = list(output.parent.glob(f"flow_{stage}_attempt_*.submit"))
            if (previous or markers or output.exists()) and previous.get("request_hash") != fingerprint:
                raise FlowCommandError(stage, "request_identity_unverified")
            if previous.get("output_sha256") and output.is_file():
                if hashlib.sha256(output.read_bytes()).hexdigest() == previous["output_sha256"]:
                    # Downstream stale-file checks use timestamps; identity was checked above.
                    output.touch()
                    if previous.get("fallback_alert_pending"):
                        self._notify_api_fallback(stage, diagnostics)
                    return output
            if previous.get("api_fallback_state"):
                raise FlowCommandError(stage, "submission_uncertain", submission_state="api_attempted")
            result = previous.get("result")
            self._write_diagnostics(diagnostics, {"request_hash": fingerprint})
            try:
                try:
                    if result is None and (previous.get("failure") or previous.get("generation_started") or markers):
                        failure = previous.get("failure") or {"category": "submission_uncertain"}
                        resume = (os.getenv("FLOW_SHADOW_RECOVERY_LOCKED") == "true" and not markers
                                  and failure.get("submission_state") == "not_attempted"
                                  and failure.get("category") in {"auth_required", "pre_submit_transient", "pre_submit_timeout"}
                                  and len(previous.get("attempts", [])) < 2)
                        if not resume:
                            raise FlowCommandError(stage, **failure)
                    self._write_diagnostics(diagnostics, {"generation_started": True})
                    generate(result)
                except Exception as error:
                    if not (os.getenv("FLOW_API_FALLBACK_ENABLED", "false").lower() == "true" and self._api_fallback_allowed(error)):
                        raise
                    self._write_diagnostics(diagnostics, {"api_fallback_state": "started", "fallback_reason": error.category})
                    client = GoogleImageClient() if stage == "image" else GoogleVideoClient()
                    client.router.retry_ambiguous_calls = False
                    if stage == "image":
                        client.generate_from_json(request, output)
                    else:
                        client.generate_from_image(request["prompt"], image_path, output)
                    self._write_diagnostics(diagnostics, {"provider": "google_api", "api_fallback_state": "complete"})
                    self._notify_api_fallback(stage, diagnostics)
                if stage == "image":
                    with Image.open(output) as image:
                        image.load()
                        if image.width < 512 or image.height < 512:
                            raise ValueError("Generated image is too small")
                else:
                    width, height, duration, audio = self._probe_video(output)
                    if (width, height) != (1080, 1920) or not 7.5 <= duration <= 8.5 or not audio:
                        raise ValueError("Canonical video has invalid dimensions, duration or audio")
                self._write_diagnostics(diagnostics, {"output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "failure": None})
                return output
            except Exception as error:
                failure = {"category": "validation_failed"}
                if isinstance(error, FlowCommandError):
                    failure = {key: getattr(error, key) for key in ("category", "error_class", "media_id", "retryable", "submission_state", "detail", "operation_id")}
                self._write_diagnostics(diagnostics, {"failure": failure})
                raise

    def _notify_api_fallback(self, stage: str, diagnostics: Path) -> None:
        from notifier import notify_warning
        from utils import get_pipeline_run_date_str
        try:
            sent = notify_warning(get_pipeline_run_date_str(), f"flow_{stage}_fallback",
                                  f"💸 Flow {stage} failed; paid Google API {stage} generation was used.")
        except Exception:
            sent = False
        self._write_diagnostics(diagnostics, {"fallback_alert_pending": not sent})

    def image_command(self, prompt_json: dict, raw_path: Path) -> list[str]:
        command = [
            "gflow", "image", "t2i", GoogleImageClient._build_prompt_from_json(prompt_json),
            "--model", "nano2", "--aspect", "3:4", "--count", "1",
            "--output", str(raw_path), "--profile", self.profile, "--json",
        ]
        if project_id := os.getenv("FLOW_PREFLIGHT_PROJECT_ID", "").strip():
            command.extend(("--project", project_id))
        return command

    def video_command(self, prompt: str, start_frame: Path, raw_path: Path) -> list[str]:
        command = [
            "gflow", "video", "i2v", "--initial-frame", str(start_frame), prompt,
            "--model", "veo-fast", "--aspect", "9:16", "--count", "1",
        ]
        if project_id := os.getenv("FLOW_PREFLIGHT_PROJECT_ID", "").strip():
            command.extend(("--project", project_id))
        if os.getenv("FLOW_VIDEO_EXPLICIT_DURATION", "false").lower() == "true":
            command.extend(("--duration", "8"))
        # The CLI renames its download to --output; keep both on the same volume.
        command.extend(("--out-dir", str(raw_path.parent), "--output", str(raw_path),
                        "--profile", self.profile, "--json"))
        return command

    @staticmethod
    def _run_json(command: list[str], timeout: int, marker: Path | None = None) -> dict:
        env = os.environ.copy()
        if marker is not None:
            env["GFLOW_SHADOW_SUBMIT_MARKER"] = str(marker)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False, env=env)
        except subprocess.TimeoutExpired:
            submitted = bool(marker and marker.exists())
            raise FlowCommandError(command[1], "submission_uncertain" if submitted else "pre_submit_timeout",
                                   submission_state="uncertain" if submitted else "not_attempted") from None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
        if result.returncode or payload.get("status") != "ok":
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            error_class = re.sub(r"[^A-Za-z0-9_]", "", str(error.get("class", "")))[:80]
            media_id = str(payload.get("media_id") or "")
            failed = payload.get("generation_status") == "MEDIA_GENERATION_STATUS_FAILED" and bool(media_id)
            reasons = (" ".join(map(str, payload.get("failure_reasons") or [])) + " " + str(error.get("class", "")) + " " + str(error.get("detail", ""))).upper()
            policy_or_quota = any(word in reasons for word in ("SAFETY", "POLICY", "REJECT", "REFUSAL", "QUOTA", "CREDIT", "RESTRICT"))
            clicked = bool(marker and marker.exists())
            submission_state = "attempted" if clicked else "not_attempted"
            if clicked:
                try:
                    submission_state = json.loads(marker.read_text()).get("state", "attempted")
                except (ValueError, OSError):
                    pass
            detail = str(error.get("detail") or "")
            for value in command[2:]:
                if len(value) > 10 and not value.startswith("--"):
                    detail = detail.replace(value, "[redacted]")
            detail = re.sub(r"https?://\S+|Bearer\s+\S+|(?:SAPISID|SID|Cookie)\s*[:=]\s*\S+|sk-or-v1-[\w-]+|AIza[\w-]+", "[redacted]", detail, flags=re.I)[:500]
            for key, value in env.items():
                if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(value) >= 8:
                    detail = detail.replace(value, "[redacted]")
            operation_id = str(payload.get("operation_id") or "")
            if not operation_id:
                catalog = FlowMediaClient._catalog_error(command[1], env.get("GFLOW_PROFILE", "shadow"))
                operation_id = catalog.get("operation_id", "")
            auth_error = error_class in {
                "AuthExpiredError", "AisandboxAuthError", "AuthMissingError",
                "AuthLoginTimeoutError", "IdentityRecheckPendingError", "FlowAccountChooserError",
            }
            if failed:
                category = "generation_failed"
            elif policy_or_quota:
                category = "provider_rejected"
            elif auth_error:
                category = "auth_required"
            elif submission_state == "blocked_before_submission":
                category = "blocked_before_submission"
            elif clicked:
                category = "post_submit_error"
            elif bool(error.get("retryable")):
                category = "pre_submit_transient"
            else:
                category = "pre_submit_terminal"
            raise FlowCommandError(command[1], category, error_class=error_class,
                                   media_id=media_id, retryable=failed and not policy_or_quota and "RECAPTCHA" not in reasons,
                                   detail=detail, operation_id=operation_id, submission_state=submission_state)
        return payload

    @staticmethod
    def _catalog_error(stage: str, profile: str) -> dict:
        try:
            from gflow_cli.config import get_settings
            db = get_settings().resolved_db_path()
            with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
                row = connection.execute("SELECT id FROM operations WHERE profile_name=? AND command=? ORDER BY rowid DESC LIMIT 1",
                                         (profile, "image t2i" if stage == "image" else "video i2v")).fetchone()
            return {"operation_id": row[0]} if row else {}
        except (ImportError, OSError, sqlite3.Error):
            return {}

    @staticmethod
    def _owned_path(raw: str, directory: Path) -> Path:
        path = Path(raw).resolve(strict=True)
        if not path.is_relative_to(directory.resolve(strict=True)) or not path.is_file():
            raise RuntimeError("gflow returned an output outside the run directory")
        return path

    @staticmethod
    def _write_diagnostics(path: Path, data: dict) -> None:
        previous = json.loads(path.read_text()) if path.exists() else {}
        staged = path.with_name(path.name + ".tmp")
        with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
            json.dump(previous | data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(path)

    def _catalog_position(self) -> int:
        try:
            from gflow_cli.config import get_settings
            db = get_settings().resolved_db_path()
            if not db.is_file():
                return 0
            with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
                return int(connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM operations").fetchone()[0])
        except (ImportError, OSError, sqlite3.Error):
            return 0

    def _catalog_video_since(self, position: int) -> dict:
        try:
            from gflow_cli.config import get_settings
            db = get_settings().resolved_db_path()
            with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT o.status, o.error_type, o.flow_project_id, a.flow_media_id, a.status, a.model, a.aspect_ratio "
                    "FROM operations o LEFT JOIN operation_assets oa ON oa.operation_id=o.id AND oa.role='output' "
                    "LEFT JOIN assets a ON a.id=oa.asset_id "
                    "WHERE o.rowid>? AND o.profile_name=? AND o.command='video i2v' "
                    "ORDER BY o.rowid DESC LIMIT 1", (position, self.profile),
                ).fetchone()
            if row:
                return dict(zip(("operation_status", "error_type", "project_id", "media_id",
                                 "asset_status", "model", "aspect"), row))
        except (ImportError, OSError, sqlite3.Error):
            pass
        return {}

    def _generate_image(self, prompt_json: dict, output_path: Path, recovered: dict | None = None) -> Path:
        started_at = datetime.now(timezone.utc).isoformat()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        diagnostics = output_path.with_name("flow_image_diagnostics.json")
        saved = json.loads(diagnostics.read_text()) if diagnostics.exists() else {}
        attempts = saved.get("attempts", [])
        previous = list(output_path.parent.glob("flow_image_attempt_*.submit"))
        marker = max(previous, key=lambda p: int(p.stem.rsplit("_", 1)[-1])) if previous else None
        payload = recovered
        first = 1 + max([int(p.stem.rsplit("_", 1)[-1]) for p in previous] +
                        [item["number"] for item in attempts], default=0)
        for number in range(first, 3) if recovered is None else ():
            marker = output_path.with_name(f"flow_image_attempt_{number}.submit")
            try:
                payload = self._run_json(self.image_command(prompt_json, output_path.with_name("flow_art.png")), 600, marker)
                self._write_diagnostics(diagnostics, {"result": {
                    "count": payload.get("count"), "model": payload.get("model"), "project_id": payload.get("project_id"),
                    "images": [{k: image.get(k) for k in ("local_path", "media_name", "model_name_type")} for image in payload.get("images", [])]}})
                attempts.append({"number": number, "submit_attempted": marker.exists(), "status": "complete"})
                self._write_diagnostics(output_path.with_name("flow_image_diagnostics.json"),
                                        {"provider": "flow", "started_at": started_at, "attempts": attempts})
                break
            except FlowCommandError as exc:
                attempts.append({"number": number, "submit_attempted": marker.exists(), "status": exc.category,
                                 "ended_at": datetime.now(timezone.utc).isoformat(),
                                 "error_class": exc.error_class, "media_id": exc.media_id,
                                 "detail": exc.detail, "operation_id": exc.operation_id,
                                 "expected_model": "NARWHAL", "submission_state": exc.submission_state})
                self._write_diagnostics(output_path.with_name("flow_image_diagnostics.json"),
                                        {"provider": "flow", "started_at": started_at, "attempts": attempts})
                if exc.category == "post_submit_error" and exc.media_id:
                    payload = {"count": 1, "model": "NARWHAL", "project_id": os.getenv("FLOW_PREFLIGHT_PROJECT_ID", ""),
                               "images": [{"local_path": None, "media_name": exc.media_id, "model_name_type": None}]}
                    self._write_diagnostics(diagnostics, {"result": payload})
                    break
                if number == 2 or exc.category not in {"pre_submit_transient", "pre_submit_timeout", "blocked_before_submission"}:
                    raise
                if marker.exists() and exc.submission_state != "blocked_before_submission":
                    raise
        images = payload.get("images") or []
        if payload.get("count") != 1 or len(images) != 1:
            raise RuntimeError("Flow did not return exactly one image")
        item = images[0]
        wire_model = item.get("model_name_type")
        try:
            submission = json.loads(marker.read_text()) if marker and marker.exists() else {}
        except (OSError, ValueError):
            submission = {}
        beluga_confirmed = (submission.get("state") == "forwarded" and
                            submission.get("expected_model") == "NARWHAL" and
                            submission.get("actual_model") == "BELUGA" and
                            submission.get("selected_model") == "Nano Banana 2.1")
        if (payload.get("model") != "NARWHAL" or
                wire_model not in (None, "BELUGA") or
                not beluga_confirmed):
            raise RuntimeError("Flow image model conflicts with Nano Banana 2 request")
        try:
            if not item.get("local_path"):
                raise FileNotFoundError("Image download missing")
            source = self._owned_path(item["local_path"], output_path.parent)
        except FileNotFoundError:
            if not item.get("media_name"):
                raise
            source = self._recover_image(item["media_name"], output_path.parent)
        source = self._owned_path(str(source), output_path.parent)
        original = source.name
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if saved.get("original_sha256") and saved["original_sha256"] != source_hash:
            raise FlowCommandError("image", "original_asset_changed")
        self._write_diagnostics(diagnostics, {"original_sha256": source_hash})
        upscaled = False
        if os.getenv("FLOW_IMAGE_UPSCALE_2K", "false").lower() == "true":
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
            "provider": "flow", "started_at": started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(), "retry_count": len(attempts) - 1,
            "model": "Nano Banana 2.1", "selected_model": submission.get("selected_model"), "wire_model": submission.get("actual_model"), "response_model": wire_model, "model_attribution_confirmed": beluga_confirmed,
            "aspect": "3:4", "source_width": width,
            "source_height": height, "flow_2k_upscaled": upscaled,
            "media_id": item.get("media_name"), "project_id": payload.get("project_id"),
            "original_file": original, "source_file": str(source.relative_to(output_path.parent.resolve())),
            "attempts": attempts,
        })
        return output_path

    @staticmethod
    def _probe_video(path: Path) -> tuple[int, int, float, bool]:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        payload = json.loads(result.stdout)
        stream = next(item for item in payload["streams"] if item["codec_type"] == "video")
        return (int(stream["width"]), int(stream["height"]), float(payload["format"]["duration"]),
                any(item["codec_type"] == "audio" for item in payload["streams"]))

    def _recover_image(self, media_id: str, output_dir: Path) -> Path:
        try:
            from gflow_cli.config import get_settings
            db = get_settings().resolved_db_path()
            with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
                rows = connection.execute(
                    "SELECT l.path, l.sha256 FROM local_files l JOIN assets a ON a.id=l.asset_id "
                    "WHERE a.profile_name=? AND a.flow_media_id=? AND a.kind='image'",
                    (self.profile, media_id)).fetchall()
            for raw, digest in rows:
                if not raw or not digest:
                    continue
                try:
                    path = self._owned_path(raw, output_dir)
                    if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                        return path
                except (OSError, RuntimeError):
                    continue
        except (ImportError, OSError, sqlite3.Error):
            pass
        # Pinned gflow data download supports videos only; never regenerate to recover an image.
        raise FlowCommandError("image", "image_recovery_unavailable", media_id=media_id)

    def _recover_video(self, media_id: str, output_dir: Path) -> Path:
        recovery_dir = output_dir / "flow_recovered"
        recovery_dir.mkdir(exist_ok=True)
        for _ in (1, 2):
            try:
                result = subprocess.run(["gflow", "data", "download", media_id, "--out", str(recovery_dir),
                                         "--profile", self.profile, "--json"], capture_output=True, text=True,
                                        timeout=300, check=False)
                if result.returncode:
                    continue
                payload = json.loads(result.stdout)
                return self._owned_path(payload["path"], output_dir)
            except (KeyError, OSError, ValueError, subprocess.TimeoutExpired):
                continue
        raise FlowCommandError("video", "existing_media_download_failed", media_id=media_id)

    def _generate_video(self, prompt_text: str, image_path: Path, output_path: Path, recovered: dict | None = None) -> Path:
        started_at = datetime.now(timezone.utc).isoformat()
        if not prompt_text.strip():
            raise ValueError("Video prompt is empty")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        start_frame = output_path.with_name("flow_start_frame.png")
        framed, _ = GoogleVideoClient._to_letterboxed_png_bytes(image_path)
        start_frame.write_bytes(framed)
        raw_path = output_path.with_name("flow_raw_video.mp4")
        diagnostics = output_path.with_name("flow_video_diagnostics.json")
        saved = json.loads(diagnostics.read_text()) if diagnostics.exists() else {}
        attempts = saved.get("attempts", [])
        previous = list(output_path.parent.glob("flow_video_attempt_*.submit"))
        submitted = len(previous)
        first = 1 + max([int(p.stem.rsplit("_", 1)[-1]) for p in previous] +
                        [item["number"] for item in attempts], default=0)
        pre_submit_retries = sum(item.get("status") in {"auth_required", "pre_submit_transient", "pre_submit_timeout"}
                                 and not item.get("submit_attempted") for item in attempts)
        payload = recovered or {}
        source = None
        if recovered:
            try:
                source = self._owned_path(payload.get("local_path") or "", output_path.parent)
            except (FileNotFoundError, RuntimeError):
                source = self._recover_video(payload["media_id"], output_path.parent)
        for number in range(first, 4) if recovered is None else ():
            marker = output_path.with_name(f"flow_video_attempt_{number}.submit")
            before = self._catalog_position()
            try:
                payload = self._run_json(self.video_command(prompt_text, start_frame, raw_path), 1500, marker)
                self._write_diagnostics(diagnostics, {"result": {k: payload.get(k) for k in ("succeeded", "media_id", "local_path") } | {
                    "request": {k: (payload.get("request") or {}).get(k) for k in ("count", "model", "mode", "aspect")}}})
                submitted = len(list(output_path.parent.glob("flow_video_attempt_*.submit")))
                attempts.append({"number": number, "status": "complete", "submit_attempted": marker.exists(),
                                 "media_id": payload.get("media_id"),
                                 "ended_at": datetime.now(timezone.utc).isoformat()})
                self._write_diagnostics(output_path.with_name("flow_video_diagnostics.json"),
                                        {"provider": "flow", "started_at": started_at,
                                         "attempts": attempts, "submissions_estimated": submitted})
                if payload.get("local_path"):
                    try:
                        source = self._owned_path(payload["local_path"], output_path.parent)
                    except FileNotFoundError:
                        source = None
                if source is None and payload.get("media_id"):
                    source = self._recover_video(str(payload["media_id"]), output_path.parent)
                    attempts[-1]["status"] = "recovered_existing_media"
                break
            except FlowCommandError as exc:
                submitted = len(list(output_path.parent.glob("flow_video_attempt_*.submit")))
                catalog = self._catalog_video_since(before) if marker.exists() else {}
                media_id = exc.media_id or catalog.get("media_id") or ""
                attempts.append({"number": number, "status": exc.category, "submit_attempted": marker.exists(),
                                 "media_id": media_id, "error_class": exc.error_class,
                                 "ended_at": datetime.now(timezone.utc).isoformat()})
                self._write_diagnostics(output_path.with_name("flow_video_diagnostics.json"),
                                        {"provider": "flow", "started_at": started_at,
                                         "attempts": attempts, "submissions_estimated": submitted})
                if (exc.category in {"pre_submit_transient", "pre_submit_timeout"} and not marker.exists()
                        and pre_submit_retries < 1 and number < 3):
                    pre_submit_retries += 1
                    continue
                if exc.category == "generation_failed" and exc.retryable and submitted < 2 and number < 3:
                    time.sleep(60)
                    continue
                if exc.category == "post_submit_error" and media_id:
                    payload = {"succeeded": True, "media_id": media_id,
                               "request": {"count": 1, "model": "veo_3_1_fast", "mode": "i2v", "aspect": "portrait"}}
                    self._write_diagnostics(diagnostics, {"result": payload})
                    source = self._recover_video(media_id, output_path.parent)
                    attempts[-1]["status"] = "recovered_existing_media"
                    break
                raise
        if source is None:
            raise FlowCommandError("video", "submission_uncertain")
        request = payload.get("request") or {}
        if (not payload.get("succeeded") or request.get("count") != 1 or
                request.get("model") != "veo_3_1_fast" or request.get("mode") != "i2v" or
                request.get("aspect") != "portrait"):
            raise RuntimeError("Flow did not return one successful video")
        width, height, duration, has_audio = self._probe_video(source)
        if (width, height) not in ((720, 1280), (1080, 1920)) or not 7.5 <= duration <= 8.5:
            raise RuntimeError(f"Flow video has invalid size or duration: {width}x{height}, {duration:.2f}s")
        if not has_audio:
            raise RuntimeError("Flow video has no audio stream")
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if saved.get("original_sha256") and saved["original_sha256"] != source_hash:
            raise FlowCommandError("video", "original_asset_changed")
        self._write_diagnostics(diagnostics, {"original_sha256": source_hash})
        if source.resolve() != raw_path.resolve():
            shutil.copyfile(source, raw_path)
        selected = source
        upscale_status = "not_needed" if width == 1080 else "disabled"
        if width == 720 and os.getenv("FLOW_VIDEO_UPSCALE_1080P", "true").lower() == "true":
            project_id = os.getenv("FLOW_PREFLIGHT_PROJECT_ID", "").strip()
            media_id = str(payload.get("media_id") or "")
            upscale_status = "unavailable"
            if project_id and media_id:
                candidate = output_path.with_name("flow_1080p_download.mp4")
                script = Path(__file__).resolve().parents[2] / "ops" / "flow_video_upscale.py"
                try:
                    result = subprocess.run([sys.executable, str(script), project_id, media_id, str(candidate)],
                                            capture_output=True, text=True, timeout=240, check=False)
                    upscale_status = json.loads(result.stdout).get("status", "unavailable")
                    if result.returncode == 0 and candidate.is_file():
                        up_width, up_height, up_duration, up_audio = self._probe_video(candidate)
                        if ((up_width, up_height) == (1080, 1920) and abs(up_duration - duration) <= 0.5
                                and up_audio == has_audio):
                            selected = candidate
                            upscale_status = "validated"
                        else:
                            upscale_status = "invalid_media"
                except (OSError, ValueError, subprocess.SubprocessError, KeyError, IndexError, StopIteration):
                    upscale_status = "invalid_media" if candidate.exists() else "unavailable"
        staged = output_path.with_name(".generated_video.staged.mp4")
        if selected == source and (width, height) == (720, 1280):
            subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-i", str(source), "-vf",
                 "scale=1080:1920:flags=lanczos", "-c:v", "libx264", "-preset", "medium",
                 "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags",
                 "+faststart", str(staged)],
                capture_output=True, timeout=600, check=True,
            )
        else:
            shutil.copyfile(selected, staged)
        staged.replace(output_path)
        self._write_diagnostics(output_path.with_name("flow_video_diagnostics.json"), {
            "provider": "flow", "started_at": started_at,
            "ended_at": datetime.now(timezone.utc).isoformat(), "retry_count": len(attempts) - 1,
            "model": "veo-fast", "aspect": "9:16", "media_id": payload.get("media_id"),
            "source_width": width, "source_height": height, "duration_seconds": duration,
            "source_has_audio": has_audio, "locally_scaled_to_1080p": selected == source and width == 720,
            "upscale_status": upscale_status, "selected_video_file": selected.name,
            "start_frame": start_frame.name, "attempts": attempts, "submissions_estimated": submitted,
        })
        return output_path
