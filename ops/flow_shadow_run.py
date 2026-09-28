#!/usr/bin/env python3
"""Run one private, non-publishing Flow experiment per pipeline date."""

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "scripts"))
from utils import get_pipeline_run_date_str


def validate_shadow_environment() -> Path:
    required = {
        "PIPELINE_MODE": "automatic",
        "PIPELINE_POST_TO_INSTAGRAM": "false",
        "MEDIA_GENERATION_PROVIDER": "flow",
        "FLOW_API_FALLBACK_ENABLED": "false",
        "GOOGLE_API_FALLBACK_ENABLED": "false",
        "PORTFOLIO_MEDIA_ENABLED": "true",
        "PIPELINE_MEDIA_MODE": "local_test",
        "OPENROUTER_CALL_DEADLINE_SECONDS": "200",
    }
    for key, expected in required.items():
        if os.getenv(key, "").strip().lower() != expected:
            raise ValueError(f"Shadow requires {key}={expected}")
    if not os.getenv("SHADOW_ALERT_BOT_TOKEN", "").strip() or not os.getenv("SHADOW_ALERT_CHAT_ID", "").strip():
        raise ValueError("Shadow alert bot token and chat ID are required")
    if os.getenv("PROMPT_GOOGLE_API_KEY", "").strip() in {"", "mock"}:
        raise ValueError("Shadow requires a prompt-only Gemini fallback key")
    forbidden = [key for key, value in os.environ.items() if value and key.startswith(("INSTAGRAM_", "VPS_", "TELEGRAM_", "GOOGLE_API_KEY_"))]
    if forbidden:
        raise ValueError(f"Shadow must not receive publishing or API keys: {', '.join(sorted(forbidden))}")
    if (ROOT / ".env").exists():
        raise ValueError("Shadow checkout must not contain a .env file")
    raw_root = os.getenv("STATE_ZERO_PRIVATE_ROOT", "").strip()
    raw_profile = os.getenv("GFLOW_CLI_HOME", "").strip()
    if not raw_root or not raw_profile or not Path(raw_root).is_absolute() or not Path(raw_profile).is_absolute():
        raise ValueError("Shadow requires absolute STATE_ZERO_PRIVATE_ROOT and GFLOW_CLI_HOME")
    private_root = Path(raw_root).resolve()
    profile_root = Path(raw_profile).resolve()
    protected = {
        ROOT.resolve(),
        (ROOT.parent / f"{ROOT.name}-private").resolve(),
        Path("/opt/state-zero-private").resolve(),
        (Path.home() / "Projects" / "state-zero-private").resolve(),
    }
    def overlaps(path: Path, boundary: Path) -> bool:
        return path == boundary or path in boundary.parents or boundary in path.parents

    if any(overlaps(private_root, boundary) for boundary in protected):
        raise ValueError("Shadow private root overlaps the repository or the production default")
    if overlaps(profile_root, private_root) or any(overlaps(profile_root, boundary) for boundary in protected):
        raise ValueError("GFLOW_CLI_HOME must be a separate shadow profile volume")
    if profile_root == Path.home() / ".local" / "share" / "gflow-cli":
        raise ValueError("Shadow must not use the default gflow browser profile")
    for directory, marker in (
        (private_root, ".state-zero-flow-shadow-private"),
        (profile_root, ".state-zero-flow-shadow-profile"),
    ):
        if not directory.is_dir() or not (directory / marker).is_file():
            raise ValueError(f"Shadow volume is missing its marker: {marker}")
    return private_root


def _write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)


def _artifact(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Required shadow artifact missing or empty: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    details = {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}
    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            details["width"], details["height"] = image.size
    elif path.suffix.lower() == ".mp4":
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        )
        media = json.loads(probe.stdout)
        video = next(stream for stream in media["streams"] if stream["codec_type"] == "video")
        details.update(width=video["width"], height=video["height"],
                       duration_seconds=round(float(media["format"]["duration"]), 3),
                       has_audio=any(stream["codec_type"] == "audio" for stream in media["streams"]))
    return details


def _run_logged(command: list[str], log: Path) -> None:
    with log.open("ab") as handle:
        result = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        script = next((Path(arg).name for arg in command[1:] if arg.endswith(".py")), command[0])
        raise RuntimeError(f"{script} exited {result.returncode}; inspect private log")


def _flow_credits() -> int | None:
    try:
        result = subprocess.run(
            ["gflow", "credits", "user", "--profile", os.getenv("GFLOW_PROFILE", "shadow"), "--json"],
            capture_output=True, text=True, check=False, timeout=45,
        )
        if result.returncode == 0:
            credits = json.loads(result.stdout).get("credits")
            if isinstance(credits, int):
                return credits
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _preflight() -> bool:
    result = subprocess.run(
        [sys.executable, str(ROOT / "ops" / "flow_shadow_preflight.py")],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=90,
    )
    print(result.stdout.strip() if result.stdout.strip() else "Flow shadow preflight failed; inspect private status")
    return result.returncode == 0


def _alert(message: str) -> None:
    token = os.getenv("SHADOW_ALERT_BOT_TOKEN", "").strip()
    chat = os.getenv("SHADOW_ALERT_CHAT_ID", "").strip()
    if not token or not chat:
        print("Shadow alert credentials missing; inspect private report")
        return
    body = urlencode({"chat_id": chat, "text": message}).encode()
    try:
        with urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body), timeout=15) as response:
            if response.status != 200:
                print("Shadow Telegram alert failed")
    except Exception:
        print("Shadow Telegram alert failed")


def _trial_summary(state: Path, start: date) -> str:
    outcomes = []
    for offset in range(7):
        day = date.fromordinal(start.toordinal() + offset).isoformat()
        marker = state / f"{day}.json"
        outcome = json.loads(marker.read_text(encoding="utf-8")).get("status", "unknown") if marker.exists() else "missing"
        outcomes.append(f"{day}: {outcome}")
    return "🏁 State Zero Flow trial: seven scheduled dates recorded.\n" + "\n".join(outcomes)


def main() -> int:
    os.umask(0o077)
    private_root = validate_shadow_environment()
    run_date = get_pipeline_run_date_str()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_date):
        raise ValueError("PIPELINE_DATE must be YYYY-MM-DD")
    current = date.fromisoformat(run_date)
    start_raw = os.getenv("SHADOW_TRIAL_START_DATE", "").strip()
    if not start_raw:
        raise ValueError("SHADOW_TRIAL_START_DATE is required")
    start = date.fromisoformat(start_raw)
    day_number = (current - start).days + 1
    if day_number < 1 or day_number > 7:
        print(f"Shadow date {run_date} is outside the seven-day trial; no generation")
        return 0
    output = private_root / "runtime" / "output" / run_date
    state = private_root / "runtime" / "state" / "flow_shadow"
    state.mkdir(parents=True, exist_ok=True)
    marker = state / f"{run_date}.json"
    if marker.exists():
        print(f"Shadow date {run_date} was already attempted; inspect its private report before any manual retry")
        return 2
    if output.exists() and any(output.iterdir()):
        raise ValueError("Shadow date output already exists; inspect it before manual retry")
    output.mkdir(parents=True, exist_ok=True)
    try:
        with os.fdopen(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            json.dump({"date": run_date, "status": "started", "started_at": datetime.now(timezone.utc).isoformat()}, handle)
    except FileExistsError:
        print(f"Shadow date {run_date} was already attempted; inspect its private report before any manual retry")
        return 2

    report = {
        "date": run_date, "trial_day": day_number, "status": "running", "provider": "flow",
        "credits_estimate_basis": "20 per Veo Fast submit marker; upper bound, failed generations may be uncharged",
        "stages": {"preflight": "pending", "pipeline": "pending", "artifacts": "pending", "archive": "pending"},
        "stage_times": {},
        "artifacts": {},
    }
    log = output / "shadow_pipeline.log"
    report["flow_credits_before"] = _flow_credits()
    stage = "preflight"
    try:
        report["stage_times"][stage] = {"started_at": datetime.now(timezone.utc).isoformat()}
        if not _preflight():
            raise RuntimeError("Flow editor sign-in or controls unavailable")
        report["stages"][stage] = "complete"
        report["stage_times"][stage]["ended_at"] = datetime.now(timezone.utc).isoformat()
        stage = "pipeline"
        report["stage_times"][stage] = {"started_at": datetime.now(timezone.utc).isoformat()}
        _run_logged([sys.executable, "-u", str(ROOT / "src" / "scripts" / "pipeline.py")], log)
        report["stages"][stage] = "complete"
        report["stage_times"][stage]["ended_at"] = datetime.now(timezone.utc).isoformat()
        stage = "artifacts"
        report["stage_times"][stage] = {"started_at": datetime.now(timezone.utc).isoformat()}
        image_diag = json.loads((output / "flow_image_diagnostics.json").read_text(encoding="utf-8"))
        original = image_diag["original_file"]
        if Path(original).name != original:
            raise RuntimeError("Flow original image path is not a filename")
        names = (
            original, "flow_raw_video.mp4", "flow_start_frame.png",
            "generated_art.png", "generated_video.mp4", "card_final.png", "card_final.mp4",
            "portfolio/light.webp", "portfolio/dark.webp", "portfolio/light.mp4", "portfolio/dark.mp4",
        )
        report["artifacts"] = {name: _artifact(output / name) for name in names}
        if (output / "flow_1080p_download.mp4").is_file():
            report["artifacts"]["flow_1080p_download.mp4"] = _artifact(output / "flow_1080p_download.mp4")
        selected = (output / image_diag["source_file"]).resolve()
        if not selected.is_relative_to(output.resolve()):
            raise RuntimeError("Flow image source escaped the shadow directory")
        if selected.name != original:
            report["artifacts"][str(selected.relative_to(output))] = _artifact(selected)
        expected_sizes = {
            "flow_start_frame.png": (1080, 1920),
            "generated_video.mp4": (1080, 1920),
            "card_final.png": (1080, 1920),
            "card_final.mp4": (1080, 1920),
            "portfolio/light.webp": (1080, 1701),
            "portfolio/dark.webp": (1080, 1701),
            "portfolio/light.mp4": (720, 1134),
            "portfolio/dark.mp4": (720, 1134),
        }
        for name, size in expected_sizes.items():
            actual = report["artifacts"][name]
            if (actual["width"], actual["height"]) != size:
                raise RuntimeError(f"Shadow artifact has incorrect dimensions: {name}")
        report["image"] = image_diag
        report["video"] = json.loads((output / "flow_video_diagnostics.json").read_text(encoding="utf-8"))
        payload = output / "last_archived_payload.json"
        if json.loads(payload.read_text(encoding="utf-8")).get("date") != run_date:
            raise RuntimeError("Archived payload date differs from shadow date")
        report["stages"][stage] = "complete"
        report["stage_times"][stage]["ended_at"] = datetime.now(timezone.utc).isoformat()
        stage = "archive"
        report["stage_times"][stage] = {"started_at": datetime.now(timezone.utc).isoformat()}
        _run_logged([sys.executable, str(ROOT / "src" / "scripts" / "database_manager.py"),
                     "--insert", "--file", str(payload)], log)
        database = private_root / "runtime" / "database" / "cards.db"
        with sqlite3.connect(database) as connection:
            found = connection.execute("SELECT 1 FROM cards WHERE date = ?", (run_date,)).fetchone()
        if not found:
            raise RuntimeError("Shadow archive missing from its database")
        report["stages"][stage] = "complete"
        report["stage_times"][stage]["ended_at"] = datetime.now(timezone.utc).isoformat()
        report["status"] = "complete"
    except Exception as exc:
        report["status"] = "failed"
        report["stages"][stage] = "failed"
        report["stage_times"].setdefault(stage, {})["ended_at"] = datetime.now(timezone.utc).isoformat()
        report["failed_stage"] = stage
        report["error_type"] = type(exc).__name__
        for kind in ("image", "video"):
            diagnostics = output / f"flow_{kind}_diagnostics.json"
            if diagnostics.is_file():
                try:
                    report[kind] = json.loads(diagnostics.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
        report["flow_credits_after"] = _flow_credits()
        report["credits_estimated_video"] = 20 * len(list(output.glob("flow_video_attempt_*.submit")))
        if report["flow_credits_before"] is not None and report["flow_credits_after"] is not None:
            report["flow_credits_observed_change"] = report["flow_credits_before"] - report["flow_credits_after"]
        _write_private_json(output / "shadow_report.json", report)
        _write_private_json(marker, {"date": run_date, "status": "failed"})
        attempts = [attempt for kind in ("image", "video") for attempt in report.get(kind, {}).get("attempts", [])]
        auth_failure = stage == "preflight" or any(attempt.get("status") == "auth_required" for attempt in attempts)
        if auth_failure:
            _alert(f"🚨 Flow sign-in needed — State Zero shadow day {day_number}/7 ({run_date}). Open the separate VPS Flow profile and rerun zero-credit preflight. API fallback is off.")
        else:
            failure_class = attempts[-1].get("status", stage) if attempts else stage
            _alert(f"🚨 State Zero Flow shadow day {day_number}/7 failed: {failure_class} ({run_date}). Inspect the private report; API fallback is off and this date will not run again automatically.")
        if day_number == 7:
            _alert(_trial_summary(state, start))
        print(f"Shadow date {run_date} failed; inspect private log and report before manual retry")
        return 1
    report["flow_credits_after"] = _flow_credits()
    report["credits_estimated_video"] = 20 * len(list(output.glob("flow_video_attempt_*.submit")))
    if report["flow_credits_before"] is not None and report["flow_credits_after"] is not None:
        report["flow_credits_observed_change"] = report["flow_credits_before"] - report["flow_credits_after"]
    _write_private_json(output / "shadow_report.json", report)
    _write_private_json(marker, {"date": run_date, "status": "complete"})
    if day_number == 7:
        _alert(_trial_summary(state, start))
    print(f"Shadow date {run_date} completed; private report: {output / 'shadow_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
