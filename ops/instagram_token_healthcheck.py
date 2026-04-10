#!/usr/bin/env python3

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from instagram_token_manager import get_instagram_token_manager
from notifier import get_notifier
from utils import ensure_path, get_pipeline_run_date_str, get_state_root, load_project_dotenv

load_project_dotenv()


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
        ) as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = handle.name
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _parse_thresholds(raw: str) -> list[int]:
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = int(part)
            if val >= 0:
                out.append(val)
        except ValueError:
            continue
    return sorted(set(out), reverse=True)


def _format_expiry_window(days_left: float | None, hours_left: float | None) -> str:
    if hours_left is not None and hours_left < 24:
        return f"{hours_left:.1f} hour(s)"
    if days_left is None:
        return "unknown"
    if float(days_left).is_integer():
        return f"{int(days_left)} day(s)"
    return f"{days_left:.1f} day(s)"


def main():
    enabled = os.getenv("INSTAGRAM_TOKEN_HEALTHCHECK_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        print("Instagram token healthcheck disabled.")
        return

    state_path = ensure_path(get_state_root()) / "instagram_token_health_state.json"
    state = _load_state(state_path)

    manager = get_instagram_token_manager()
    notifier = get_notifier()
    report = manager.inspect_token_health()

    run_date = get_pipeline_run_date_str()
    state["last_checked_at"] = report.get("checked_at")
    state["last_report"] = report
    state["auto_refresh_mode"] = manager.auto_refresh_mode

    refresh_failed_on_invalid = False
    consecutive_refresh_failures = int(state.get("consecutive_refresh_failures") or 0)
    if manager.auto_refresh_mode == "hybrid":
        refreshed, refresh_msg = manager.maybe_auto_refresh(report=report, force_on_invalid=not report.get("valid"))
        state["last_refresh_attempt_msg"] = refresh_msg
        if refreshed:
            state["consecutive_refresh_failures"] = 0
            state["last_refresh_success_at"] = datetime.now().isoformat()
            state["last_refresh_failure_at"] = None
            state["last_refresh_failure_reason"] = None
            state["last_refresh_failure_notice_key"] = None
            report = manager.inspect_token_health()
            state["last_report"] = report
            notifier.notify_status(
                run_date=run_date,
                status="TOKEN_REFRESH_SUCCESS",
                message=f"Instagram token refreshed automatically ({refresh_msg}).",
            )
        elif refresh_msg.startswith("refresh failed"):
            consecutive_refresh_failures += 1
            state["consecutive_refresh_failures"] = consecutive_refresh_failures
            state["last_refresh_failure_at"] = datetime.now().isoformat()
            state["last_refresh_failure_reason"] = refresh_msg

            failure_bucket = "3+" if consecutive_refresh_failures >= 3 else str(consecutive_refresh_failures)
            notice_key = f"{failure_bucket}:{report.get('valid')}:{refresh_msg}"
            if state.get("last_refresh_failure_notice_key") != notice_key:
                if not report.get("valid") or consecutive_refresh_failures >= 3:
                    notifier.notify_error(
                        run_date=run_date,
                        step="InstagramTokenHealth",
                        error_type="TokenRefreshFailure",
                        message=(
                            f"Instagram token refresh failed {consecutive_refresh_failures} consecutive time(s). "
                            "Manual intervention is likely required."
                        ),
                        details_tail=refresh_msg,
                        fatal=False,
                    )
                elif consecutive_refresh_failures == 2:
                    notifier.notify_warning(
                        run_date=run_date,
                        step="InstagramTokenHealth",
                        message="Automatic Instagram token refresh has failed twice consecutively.",
                        details_tail=refresh_msg,
                    )
                else:
                    notifier.notify_warning(
                        run_date=run_date,
                        step="InstagramTokenHealth",
                        message="Automatic Instagram token refresh failed.",
                        details_tail=refresh_msg,
                    )
                state["last_refresh_failure_notice_key"] = notice_key

            if not report.get("valid"):
                refresh_failed_on_invalid = True

    if not report.get("valid"):
        detail = report.get("detail", "Instagram token invalid.")
        if refresh_failed_on_invalid:
            failure_count = int(state.get("consecutive_refresh_failures") or 0)
            detail = f"{detail} | Auto-refresh attempt failed ({failure_count} consecutive failure(s))."
        notifier.notify_error(
            run_date=run_date,
            step="InstagramTokenHealth",
            error_type="TokenInvalid",
            message=detail,
            fatal=False,
        )
        _save_state(state_path, state)
        print("Token healthcheck: invalid.")
        return

    thresholds = _parse_thresholds(os.getenv("INSTAGRAM_TOKEN_ALERT_DAYS", "14,7,3,1"))
    days_left = report.get("days_to_expiry")
    hours_left = report.get("hours_to_expiry")
    expires_at = report.get("expires_at")

    if days_left is None or not expires_at:
        msg = "Instagram token is valid. Expiry date unavailable (set FACEBOOK_APP_ID/SECRET for expiry alerts)."
        if state.get("last_no_expiry_notice_date") != run_date:
            notifier.notify_status(
                run_date=run_date,
                status="TOKEN_HEALTH_OK",
                message=msg,
            )
            state["last_no_expiry_notice_date"] = run_date
        _save_state(state_path, state)
        print("Token healthcheck: valid, expiry unknown.")
        return

    state["last_expires_at"] = expires_at
    state["last_days_to_expiry"] = days_left
    state["last_hours_to_expiry"] = hours_left

    crossed = None
    for threshold in thresholds:
        if days_left <= threshold:
            crossed = threshold
            break

    dedupe_key = f"{expires_at}:{crossed}"
    if crossed is not None and state.get("last_expiry_alert_key") != dedupe_key:
        notifier.notify_warning(
            run_date=run_date,
            step="InstagramTokenHealth",
            message=(
                f"Instagram token expires in {_format_expiry_window(days_left, hours_left)} "
                f"(threshold {crossed} day(s)). Rotate/refresh soon."
            ),
            details_tail=f"expires_at={expires_at}",
        )
        state["last_expiry_alert_key"] = dedupe_key
    elif state.get("last_ok_notice_date") != run_date:
        notifier.notify_status(
            run_date=run_date,
            status="TOKEN_HEALTH_OK",
            message=f"Instagram token valid; expires in {_format_expiry_window(days_left, hours_left)}.",
        )
        state["last_ok_notice_date"] = run_date

    _save_state(state_path, state)
    print(f"Token healthcheck: valid, {_format_expiry_window(days_left, hours_left)} left.")


if __name__ == "__main__":
    main()
