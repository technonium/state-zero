#!/usr/bin/env python3
"""Run one private, non-publishing Flow experiment per pipeline date."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
LOOKUP_PENDING = 2
LOOKUP_RETRYABLE = 3
LOOKUP_TERMINAL = 4
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


def _append_private_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
    path.chmod(0o600)


def classify_whoop_readiness(returncode: int, output: str) -> str:
    text = output.lower()
    if returncode == 0:
        return "ready"
    if returncode == LOOKUP_PENDING:
        return "waiting_for_whoop"
    if returncode == LOOKUP_RETRYABLE:
        if any(term in text for term in ("401", "auth error", "reauth", "token refresh failed", "authorization could not be recovered")):
            return "reauth_required"
        return "retryable_whoop_error"
    if returncode == LOOKUP_TERMINAL:
        return "terminal_configuration_error"
    return "terminal_lookup_error"


def trial_day_number(start: date, current: date) -> int:
    return (current - start).days + 1


def schedule_allows_run(final_check: bool, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(os.getenv("PIPELINE_TIMEZONE", "Asia/Kolkata")))
    minute_of_day = local.hour * 60 + local.minute
    if final_check:
        return 15 * 60 + 15 <= minute_of_day < 15 * 60 + 45
    return 10 * 60 <= minute_of_day <= 15 * 60


def _acquire_runner_lock(state: Path):
    handle = (state / "runner.lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BlockingIOError:
        handle.close()
        return None


class MemorySampler:
    """Sample cgroup memory during a run; keep it best-effort on other hosts."""

    def __init__(self, evidence_path: Path | None = None):
        self.evidence_path = evidence_path
        self.stop_event = threading.Event()
        self.values: dict[str, int | None] = {
            "peak_bytes": None,
            "minimum_host_mem_available_kb": None,
        }
        self.before_events = self._oom_events()
        self.last_events = self.before_events.copy()
        self.last_persisted_at = 0.0
        self.thread = threading.Thread(target=self._sample, daemon=True)

    @staticmethod
    def _read_int(path: Path) -> int | None:
        try:
            return int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    @classmethod
    def _oom_events(cls) -> dict[str, int]:
        try:
            pairs = (line.split() for line in Path("/sys/fs/cgroup/memory.events").read_text().splitlines())
            return {key: int(value) for key, value in pairs if key in {"oom", "oom_kill"}}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _host_mem_available() -> int | None:
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1])
        except (OSError, ValueError, IndexError):
            pass
        return None

    def _sample_once(self, force_persist: bool = False) -> None:
        current = self._read_int(Path("/sys/fs/cgroup/memory.current"))
        available = self._host_mem_available()
        if current is not None:
            self.values["peak_bytes"] = max(self.values["peak_bytes"] or 0, current)
        if available is not None:
            previous = self.values["minimum_host_mem_available_kb"]
            self.values["minimum_host_mem_available_kb"] = min(previous or available, available)
        events = self._oom_events()
        observed_at = time.monotonic()
        if self.evidence_path and (force_persist or observed_at - self.last_persisted_at >= 10 or events != self.last_events):
            _append_private_jsonl(self.evidence_path, {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "peak_bytes": self.values["peak_bytes"],
                "minimum_host_mem_available_kb": self.values["minimum_host_mem_available_kb"],
                "oom_events": events,
                "oom_events_delta": {key: max(0, value - self.before_events.get(key, 0)) for key, value in events.items()},
            })
            self.last_persisted_at = observed_at
        self.last_events = events

    def _sample(self) -> None:
        while not self.stop_event.is_set():
            self._sample_once()
            if self.stop_event.wait(1):
                break

    def start(self) -> None:
        self._sample_once(force_persist=True)
        self.thread.start()

    def stop(self) -> dict:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        self._sample_once(force_persist=True)
        after_events = self._oom_events()
        return {
            **self.values,
            "oom_events_delta": {key: max(0, value - self.before_events.get(key, 0)) for key, value in after_events.items()},
        }


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


def _run_logged(command: list[str], log: Path, extra_env: dict[str, str] | None = None) -> None:
    environment = os.environ.copy()
    if extra_env:
        environment.update(extra_env)
    with log.open("ab") as handle:
        result = subprocess.run(command, cwd=ROOT, env=environment, stdout=handle, stderr=subprocess.STDOUT, check=False)
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
    for attempt in range(2):
        result = subprocess.run(
            [sys.executable, str(ROOT / "ops" / "flow_shadow_preflight.py")],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=240,
        )
        status = result.stdout.strip()
        print(status if status else "Flow shadow preflight failed; inspect private status")
        if result.returncode == 0:
            return True
        if attempt == 0 and status in {
            "Flow shadow preflight: browser_error",
            "Flow shadow preflight: unknown_editor",
            "Flow shadow preflight: controls_unavailable",
        }:
            continue
        return False
    return False


def _probe_whoop(run_date: str, output: Path) -> tuple[str, int, str]:
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "src" / "scripts" / "lookups.py"), "--date", run_date],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=240,
        )
    except subprocess.TimeoutExpired as error:
        partial = "WHOOP lookup timed out"
        _append_private_jsonl(output / "whoop_readiness.jsonl", {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "retryable_whoop_error",
            "exit_code": None,
            "error": partial,
        })
        return "retryable_whoop_error", LOOKUP_RETRYABLE, partial
    except OSError as error:
        message = f"WHOOP lookup could not start ({type(error).__name__})"
        _append_private_jsonl(output / "whoop_readiness.jsonl", {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "terminal_configuration_error",
            "exit_code": None,
            "error": message,
        })
        return "terminal_configuration_error", LOOKUP_TERMINAL, message
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    with (output / "whoop_readiness.log").open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now(timezone.utc).isoformat()}] exit={result.returncode}\n{combined}\n")
    (output / "whoop_readiness.log").chmod(0o600)
    status = classify_whoop_readiness(result.returncode, combined)
    if status == "ready":
        data_path = output / "daily_data.json"
        if not data_path.is_file():
            status = "terminal_lookup_error"
            combined = "WHOOP lookup returned success without daily_data.json"
        else:
            try:
                payload = json.loads(data_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                status = "terminal_lookup_error"
                combined = "WHOOP lookup produced invalid daily_data.json"
            else:
                if not isinstance(payload, dict) or payload.get("date") != run_date:
                    status = "terminal_lookup_error"
                    combined = "WHOOP lookup returned a different date"
    _append_private_jsonl(output / "whoop_readiness.jsonl", {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "exit_code": result.returncode,
    })
    return status, result.returncode, combined


def _alert(message: str) -> bool:
    token = os.getenv("SHADOW_ALERT_BOT_TOKEN", "").strip()
    chat = os.getenv("SHADOW_ALERT_CHAT_ID", "").strip()
    if not token or not chat:
        print("Shadow alert credentials missing; inspect private report")
        return False
    body = urlencode({"chat_id": chat, "text": message}).encode()
    try:
        with urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body), timeout=15) as response:
            if response.status == 200:
                return True
            print("Shadow Telegram alert failed")
    except Exception:
        print("Shadow Telegram alert failed")
    return False


def _alert_once(state: Path, run_date: str, key: str, message: str) -> bool:
    marker = state / "alerts" / f"{run_date}.json"
    try:
        sent = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
    except (OSError, ValueError):
        sent = {}
    previous = sent.get(key)
    if isinstance(previous, str) or isinstance(previous, dict) and previous.get("sent_at"):
        return False
    if not _alert(message):
        sent[key] = {"pending": True, "message": message, "last_attempt_at": datetime.now(timezone.utc).isoformat()}
        _write_private_json(marker, sent)
        return False
    sent[key] = {"sent_at": datetime.now(timezone.utc).isoformat()}
    _write_private_json(marker, sent)
    return True


def _retry_pending_alerts(state: Path) -> None:
    for marker in sorted((state / "alerts").glob("*.json")):
        try:
            sent = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(sent, dict):
            continue
        changed = False
        for key, entry in sent.items():
            if not isinstance(entry, dict) or not entry.get("pending"):
                continue
            message = entry.get("message")
            if not isinstance(message, str) or not message:
                continue
            if _alert(message):
                sent[key] = {"sent_at": datetime.now(timezone.utc).isoformat()}
                changed = True
        if changed:
            _write_private_json(marker, sent)


def _trial_summary(state: Path, start: date) -> str:
    outcomes = []
    for offset in range(7):
        day = date.fromordinal(start.toordinal() + offset).isoformat()
        marker = state / f"{day}.json"
        try:
            payload = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        except (OSError, ValueError):
            payload = {}
        outcome = payload.get("status", "unknown") if isinstance(payload, dict) else "unknown"
        if not marker.exists():
            outcome = "missing"
        outcomes.append(f"{day}: {outcome}")
    return "🏁 State Zero Flow: first seven daily runs\n" + "\n".join(outcomes)


def maybe_send_trial_summary(state: Path, start: date, current: date) -> bool:
    if trial_day_number(start, current) < 7:
        return False
    marker = state / "trial_summary_sent.json"
    if marker.exists():
        return False
    if not _alert(_trial_summary(state, start)):
        return False
    _write_private_json(marker, {"sent_at": datetime.now(timezone.utc).isoformat()})
    return True


def _record_final_miss(state: Path, run_date: str, status: str) -> None:
    marker = state / f"{run_date}.json"
    _write_private_json(marker, {
        "date": run_date,
        "status": "missed",
        "reason": status,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })
    _alert_once(
        state, run_date, "final_missed",
        f"⚠️ State Zero Flow missed {run_date}: WHOOP data was unavailable at the 15:15 IST final check ({status}). No media was submitted. Check the separate WHOOP authorization or wait for the next day's run.",
    )


def _handle_existing_date_marker(state: Path, marker: Path, run_date: str, start: date, current: date, final_check: bool) -> None:
    try:
        existing = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = {}
    status = existing.get("status") if isinstance(existing, dict) else None
    if status == "missed" and final_check:
        _record_final_miss(state, run_date, str(existing.get("reason", "unknown")))
    elif status in {"started", "running", "uncertain"} or status is None:
        _write_private_json(marker, {
            "date": run_date,
            "status": "uncertain",
            "previous_status": status or "invalid_marker",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        })
        _alert_once(
            state, run_date, "uncertain_run",
            f"🚨 State Zero Flow run for {run_date} stopped without a final report. Its generation status is uncertain, so it will not be retried automatically. Inspect the private Flow report and media IDs before any manual recovery.",
        )
    if trial_day_number(start, current) >= 7:
        maybe_send_trial_summary(state, start, current)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the isolated Oracle Flow pipeline")
    parser.add_argument("--final-check", action="store_true", help="record a missed date if WHOOP data is still unavailable")
    parser.add_argument("--manual", action="store_true", help="run outside the scheduled IST window for supervised validation")
    parser.add_argument("--recover-date", help="supervised media-only recovery using saved inputs; preserves original failure evidence")
    parser.add_argument("--replace-image", action="store_true", help="authorize one replacement after reconciling prior image submissions")
    args = parser.parse_args(argv)
    if args.replace_image and not args.recover_date:
        parser.error("--replace-image requires --recover-date")
    os.umask(0o077)
    private_root = validate_shadow_environment()
    run_date = args.recover_date or get_pipeline_run_date_str()
    if args.recover_date:
        os.environ["PIPELINE_DATE"] = run_date
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", run_date):
        raise ValueError("PIPELINE_DATE must be YYYY-MM-DD")
    current = date.fromisoformat(run_date)
    start_raw = os.getenv("SHADOW_TRIAL_START_DATE", "").strip()
    if not start_raw:
        raise ValueError("SHADOW_TRIAL_START_DATE is required")
    start = date.fromisoformat(start_raw)
    day_number = trial_day_number(start, current)
    if day_number < 1:
        print(f"Shadow date {run_date} is before the configured start date; no generation")
        return 0
    output = private_root / "runtime" / "output" / run_date
    state = private_root / "runtime" / "state" / "flow_shadow"
    state.mkdir(parents=True, exist_ok=True)
    marker = state / f"{run_date}.json"
    lock_file = _acquire_runner_lock(state)
    if lock_file is None:
        print("Another Flow shadow check or run is active; skipping this schedule tick")
        return 0
    try:
        return _run_locked(args, private_root, run_date, current, start, day_number, output, state, marker)
    finally:
        lock_file.close()


def _run_locked(args, private_root: Path, run_date: str, current: date, start: date, day_number: int, output: Path, state: Path, marker: Path) -> int:
    now = datetime.now(ZoneInfo(os.getenv("PIPELINE_TIMEZONE", "Asia/Kolkata")))
    recovery = bool(getattr(args, "recover_date", None))
    original_marker = marker
    report_name = "shadow_recovery_report.json" if recovery else "shadow_report.json"
    if recovery:
        original = json.loads(marker.read_text()) if marker.exists() else {}
        if original.get("status") != "failed":
            raise ValueError("Recovery requires an existing failed date")
        marker = state / "recoveries" / f"{run_date}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        original_evidence = marker.with_suffix(".original.json")
        if not original_evidence.exists():
            _write_private_json(original_evidence, original)
        from flow_shadow_recover import validate_recovery_inputs
        validate_recovery_inputs(output, getattr(args, "replace_image", False))
        required_inputs = ("daily_data.json", "card_metadata.json", "image_prompt.json", "video_prompt.txt")
        if any(not (output / name).is_file() for name in required_inputs):
            raise ValueError("Recovery requires all saved inputs")
    if not recovery and not args.manual and not schedule_allows_run(args.final_check, now):
        print("Flow shadow schedule tick is outside its IST window; no WHOOP or Flow request")
        return 0
    _retry_pending_alerts(state)
    if day_number > 7:
        maybe_send_trial_summary(state, start, current)
    if marker.exists():
        _handle_existing_date_marker(state, marker, run_date, start, current, args.final_check)
        print(f"Shadow date {run_date} already has a terminal or claimed status; no second full run")
        return 2
    output.mkdir(parents=True, exist_ok=True)

    readiness = "ready"
    if not recovery:
        readiness, _, _ = _probe_whoop(run_date, output)
    if readiness != "ready":
        print(f"WHOOP readiness: {readiness}; no Flow request was made")
        if args.final_check:
            _record_final_miss(state, run_date, readiness)
            maybe_send_trial_summary(state, start, current)
            return 1 if readiness.startswith("terminal_") else 0
        if readiness == "reauth_required":
            _alert_once(
                state, run_date, "whoop_reauth",
                f"🚨 WHOOP reauthorization needed for State Zero Flow on {run_date}. Refresh the separate shadow WHOOP authorization. No Flow media was submitted.",
            )
        elif readiness in {"terminal_configuration_error", "terminal_lookup_error"}:
            _alert_once(
                state, run_date, "whoop_configuration",
                f"🚨 State Zero Flow WHOOP setup needs attention on {run_date} ({readiness}). Inspect the private readiness log. No Flow media was submitted.",
            )
        return 0 if readiness in {"waiting_for_whoop", "retryable_whoop_error", "reauth_required"} else 1

    generated_names = ("generated_art.png", "generated_video.mp4", "flow_raw_video.mp4", "card_final.png", "card_final.mp4", "last_archived_payload.json")
    submitted = list(output.glob("flow_image_attempt_*.submit")) + list(output.glob("flow_video_attempt_*.submit"))
    if not recovery and (submitted or any((output / name).exists() for name in generated_names)):
        _alert_once(
            state, run_date, "output_collision",
            f"🚨 State Zero Flow found existing generation artifacts for {run_date} without a runner claim. It stopped without submitting media; inspect the private output before recovery.",
        )
        return 1

    try:
        with os.fdopen(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
            json.dump({"date": run_date, "status": "started", "started_at": datetime.now(timezone.utc).isoformat()}, handle)
    except FileExistsError:
        print(f"Shadow date {run_date} was claimed by another invocation; no second full run")
        return 2

    report = {
        "date": run_date, "trial_day": day_number, "status": "running", "provider": "flow", "recovery": recovery,
        "credits_estimate_basis": "20 per Veo Fast submit marker; upper bound, failed generations may be uncharged",
        "stages": {"preflight": "pending", "pipeline": "pending", "artifacts": "pending", "archive": "pending"},
        "stage_times": {},
        "artifacts": {},
        "memory_samples_file": "recovery_memory_samples.jsonl" if recovery else "memory_samples.jsonl",
    }
    memory = MemorySampler(output / report["memory_samples_file"])
    memory.start()
    log = output / ("shadow_recovery.log" if recovery else "shadow_pipeline.log")
    report["flow_credits_before"] = _flow_credits()
    stage = "preflight"
    try:
        report["stage_times"][stage] = {"started_at": datetime.now(timezone.utc).isoformat()}
        (state / "preflight.json").unlink(missing_ok=True)
        if not _preflight():
            raise RuntimeError("Flow editor sign-in or controls unavailable")
        report["stages"][stage] = "complete"
        report["stage_times"][stage]["ended_at"] = datetime.now(timezone.utc).isoformat()
        stage = "pipeline"
        report["stage_times"][stage] = {"started_at": datetime.now(timezone.utc).isoformat()}
        _run_logged(
            [sys.executable, "-u", str(ROOT / "ops" / "flow_shadow_recover.py" if recovery else ROOT / "src" / "scripts" / "pipeline.py")], log,
            {"FLOW_SHADOW_WHOOP_PREFETCHED": "true", "FLOW_SHADOW_ALERTS_OWNED_BY_RUNNER": "true",
             "FLOW_SHADOW_REPLACE_IMAGE": "true" if recovery and getattr(args, "replace_image", False) else "false",
             "FLOW_SHADOW_RECOVERY_LOCKED": "true" if recovery else "false"},
        )
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
        report["memory"] = memory.stop()
        _write_private_json(output / report_name, report)
        _write_private_json(marker, {"date": run_date, "status": "failed"})
        attempts = [attempt for kind in ("image", "video") for attempt in report.get(kind, {}).get("attempts", [])]
        preflight_status = "unknown"
        if stage == "preflight":
            try:
                raw_status = json.loads((state / "preflight.json").read_text(encoding="utf-8")).get("status", "unknown")
                preflight_status = re.sub(r"[^a-z0-9_]", "", str(raw_status))[:40] or "unknown"
            except (OSError, ValueError):
                pass
        auth_failure = preflight_status in {"reauth_required", "chrome_profile_missing"} or any(
            attempt.get("status") == "auth_required" for attempt in attempts
        )
        if auth_failure:
            _alert_once(state, run_date, "flow_auth", f"🚨 Flow sign-in needed — State Zero shadow day {day_number} ({run_date}). Open the separate VPS Flow profile and rerun zero-credit preflight. API fallback is off.")
        elif stage == "preflight":
            _alert_once(state, run_date, "flow_preflight", f"🚨 Flow editor needs attention — State Zero shadow day {day_number} ({run_date}): {preflight_status}. Inspect the private preflight report; no media was submitted.")
        else:
            failure_class = attempts[-1].get("status", stage) if attempts else stage
            media_stage = next((kind for kind in ("video", "image") if report.get(kind, {}).get("attempts")), stage)
            reason = "model/settings validation failed" if attempts and "model" in attempts[-1].get("detail", "") else failure_class
            _alert_once(state, run_date, "pipeline_failure",
                f"🚨 State Zero Flow day {day_number} ({run_date}): {media_stage} failed — {reason}. "
                "Inspect the private report and reconcile existing Flow media before recovery. Automatic regeneration is blocked; API fallback is off.")
        maybe_send_trial_summary(state, start, current)
        print(f"Shadow date {run_date} failed; inspect private log and report before manual retry")
        return 1
    report["flow_credits_after"] = _flow_credits()
    report["credits_estimated_video"] = 20 * len(list(output.glob("flow_video_attempt_*.submit")))
    if report["flow_credits_before"] is not None and report["flow_credits_after"] is not None:
        report["flow_credits_observed_change"] = report["flow_credits_before"] - report["flow_credits_after"]
    report["memory"] = memory.stop()
    _write_private_json(output / report_name, report)
    _write_private_json(marker, {"date": run_date, "status": "complete"})
    if recovery:
        _write_private_json(original_marker, {"date": run_date, "status": "complete", "recovered": True,
                                            "original_report": "shadow_report.json", "recovery_report": report_name})
    maybe_send_trial_summary(state, start, current)
    print(f"Shadow date {run_date} completed; private report: {output / report_name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        _alert("🚨 State Zero Flow shadow stopped unexpectedly. Check the separate service logs and submit markers before any retry.")
        raise
