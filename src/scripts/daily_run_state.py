import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from utils import ensure_path, get_state_root


ACTIVE_STATUSES = {"STARTING", "WAITING_MANUAL", "AUTO_FALLBACK_RUNNING", "POSTING"}
TERMINAL_STATUSES = {"POSTED", "FAILED_FATAL"}


class OwnershipLostError(RuntimeError):
    pass


class DailyRunStateManager:
    def __init__(
        self,
        *,
        run_date: str,
        timezone_name: str,
        run_token: str,
        stale_after: timedelta | None = None,
    ):
        self.run_date = run_date
        self.tz = ZoneInfo(timezone_name)
        self.run_token = run_token
        self.stale_after = stale_after or timedelta(minutes=20)
        self.state_dir = ensure_path(get_state_root() / "daily_runs")
        self.state_path = self.state_dir / f"{run_date}.json"
        self.claim_path = self.state_dir / f"{run_date}.claim"
        self._owns_claim = False

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def now_iso(self) -> str:
        return self.now().isoformat()

    def load_state(self) -> dict | None:
        if not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def load_claim(self) -> dict | None:
        if not self.claim_path.exists():
            return None
        try:
            raw = self.claim_path.read_text(encoding="utf-8").strip()
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            return None

    def is_owner(self) -> bool:
        return self._owns_claim

    def current_status(self) -> str | None:
        state = self.load_state() or {}
        status = state.get("status")
        return str(status).strip().upper() if status else None

    def is_posted(self) -> bool:
        return self.current_status() == "POSTED"

    def acquire(self) -> tuple[str, dict | None]:
        for _ in range(3):
            state = self.load_state() or {}
            status = str(state.get("status") or "").strip().upper()
            if status == "POSTED":
                return "skip_posted", state
            if status == "FAILED_FATAL":
                return "skip_failed_fatal", state

            if self._try_create_claim():
                state = self._become_owner()
                self._owns_claim = True
                return "owner", state

            claim = self.load_claim() or {}
            state = self.load_state() or {}
            status = str(state.get("status") or "").strip().upper()
            if status in ACTIVE_STATUSES and self._has_fresh_heartbeat(state):
                return "skip_active", state
            if not status and self._has_fresh_claim(claim):
                return "skip_active", state or claim

            self._remove_claim_if_present()

        return "skip_active", self.load_state()

    def heartbeat(self, *, status: str | None = None, note: str | None = None, extra: dict | None = None) -> dict:
        return self._update_state(status=status, note=note, extra=extra, touch_heartbeat=True)

    def update_status(self, *, status: str, note: str | None = None, extra: dict | None = None) -> dict:
        return self._update_state(status=status, note=note, extra=extra, touch_heartbeat=True)

    def mark_retryable_failure(self, *, step: str, message: str, details_tail: str | None = None):
        extra = {
            "last_error_step": step,
            "last_error_message": message,
            "last_error_details_tail": details_tail,
            "retryable": True,
        }
        self._update_state(status="FAILED_RETRYABLE", note=message, extra=extra, touch_heartbeat=True)

    def mark_fatal_failure(self, *, step: str, message: str, details_tail: str | None = None):
        state = self.load_state() or {}
        if str(state.get("status") or "").strip().upper() == "POSTED":
            return

        extra = {
            "last_error_step": step,
            "last_error_message": message,
            "last_error_details_tail": details_tail,
            "retryable": False,
        }
        self._update_state(status="FAILED_FATAL", note=message, extra=extra, touch_heartbeat=True)

    def mark_posted(
        self,
        *,
        post_id: str | None,
        permalink: str | None,
        note: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        payload = {
            "instagram_post_id": post_id,
            "instagram_permalink": permalink,
            "posted_at": self.now_iso(),
        }
        if extra:
            payload.update(extra)
        return self._update_state(status="POSTED", note=note, extra=payload, touch_heartbeat=True)

    def release_claim(self):
        claim = self.load_claim() or {}
        claim_token = claim.get("run_token")
        if claim_token and claim_token != self.run_token:
            self._owns_claim = False
            return
        try:
            self.claim_path.unlink()
        except FileNotFoundError:
            pass
        self._owns_claim = False

    def delete_state(self, *, only_if_run_token_matches: bool = True):
        state = self.load_state() or {}
        state_token = state.get("run_token")
        if only_if_run_token_matches and state_token and state_token != self.run_token:
            return
        try:
            self.state_path.unlink()
        except FileNotFoundError:
            pass

    def _become_owner(self) -> dict:
        previous = self.load_state() or {}
        now_iso = self.now_iso()
        state = {
            "date": self.run_date,
            "status": "STARTING",
            "run_token": self.run_token,
            "timezone": str(self.tz),
            "claimed_at": now_iso,
            "last_heartbeat_at": now_iso,
            "updated_at": now_iso,
            "owner_pid": os.getpid(),
            "note": "Claimed daily run ownership.",
        }
        if previous.get("created_at"):
            state["created_at"] = previous["created_at"]
        else:
            state["created_at"] = now_iso
        prior_token = previous.get("run_token")
        if prior_token and prior_token != self.run_token:
            state["reclaimed_from_run_token"] = prior_token
        if previous.get("status") == "FAILED_RETRYABLE":
            state["previous_retryable_failure_at"] = previous.get("updated_at")
        self._write_json_atomic(self.state_path, state)
        return state

    def _try_create_claim(self) -> bool:
        payload = {
            "date": self.run_date,
            "run_token": self.run_token,
            "claimed_at": self.now_iso(),
            "owner_pid": os.getpid(),
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.claim_path, flags, 0o644)
        except FileExistsError:
            return False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            self.claim_path.unlink(missing_ok=True)
            raise
        return True

    def _remove_claim_if_present(self):
        try:
            self.claim_path.unlink()
        except FileNotFoundError:
            pass

    def _parse_dt(self, raw: str | None) -> datetime | None:
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=self.tz)
        return parsed.astimezone(self.tz)

    def _has_fresh_heartbeat(self, state: dict) -> bool:
        last = self._parse_dt(state.get("last_heartbeat_at") or state.get("updated_at"))
        if not last:
            return False
        return (self.now() - last) <= self.stale_after

    def _has_fresh_claim(self, claim: dict) -> bool:
        claimed_at = self._parse_dt(claim.get("claimed_at"))
        if not claimed_at:
            if self.claim_path.exists():
                claimed_at = datetime.fromtimestamp(self.claim_path.stat().st_mtime, tz=self.tz)
            else:
                return False
        return (self.now() - claimed_at) <= self.stale_after

    def _assert_owner(self):
        if not self._owns_claim:
            raise OwnershipLostError("This process no longer owns the daily run claim.")

        state = self.load_state() or {}
        state_token = state.get("run_token")
        if state_token and state_token != self.run_token:
            self._owns_claim = False
            raise OwnershipLostError(
                f"Daily run ownership moved to another token ({state_token}); current token is {self.run_token}."
            )

    def _update_state(
        self,
        *,
        status: str | None,
        note: str | None,
        extra: dict | None,
        touch_heartbeat: bool,
    ) -> dict:
        self._assert_owner()
        state = self.load_state() or {"date": self.run_date, "created_at": self.now_iso()}
        current_status = str(state.get("status") or "").strip().upper()
        if current_status in TERMINAL_STATUSES and status and status != current_status:
            return state

        now_iso = self.now_iso()
        state["date"] = self.run_date
        state["run_token"] = self.run_token
        state["timezone"] = str(self.tz)
        state["owner_pid"] = os.getpid()
        state["updated_at"] = now_iso
        if touch_heartbeat:
            state["last_heartbeat_at"] = now_iso
        if status:
            state["status"] = status
        if note is not None:
            state["note"] = note
        if extra:
            state.update(extra)
        self._write_json_atomic(self.state_path, state)
        return state

    def _write_json_atomic(self, path: Path, payload: dict):
        tmp_path = path.with_suffix(f"{path.suffix}.tmp.{self.run_token}")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
