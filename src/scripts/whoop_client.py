import logging
import os
from datetime import date, datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo
import httpx
from whoop_token_manager import WHOOPTokenManager, WhoopReauthRequired

logger = logging.getLogger(__name__)

class WhoopAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"WHOOP API Error ({status_code}): {message}")


class WhoopDailyDataPendingError(WhoopAPIError):
    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(404, message)

class WHOOPClient:
    DEFAULT_CYCLE_FETCH_LIMIT = 25
    EXPANDED_CYCLE_FETCH_LIMIT = 100
    DEFAULT_CYCLE_LOOKAHEAD_DAYS = 2
    EXPANDED_CYCLE_LOOKBACK_DAYS = (7, 14, 30)

    def __init__(self):
        self.base_url = "https://api.prod.whoop.com/developer"
        self.token_manager = WHOOPTokenManager.get_instance()
        self.local_tz = ZoneInfo("Asia/Kolkata")

    async def _get_headers(self) -> dict:
        access_token = await self.token_manager.get_valid_token()
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, params: dict = None, _retry: bool = True) -> dict:
        async with httpx.AsyncClient() as client:
            headers = await self._get_headers()
            response = await client.request(method, f"{self.base_url}{path}", headers=headers, params=params, timeout=30.0)

            if response.status_code == 401 and _retry:
                logger.info("401 Unauthorized, refreshing token...")
                refreshed = await self.token_manager._refresh_token()
                if not refreshed:
                    raise WhoopAPIError(401, "Token refresh failed; authorization could not be recovered automatically")
                headers = await self._get_headers()
                response = await client.request(method, f"{self.base_url}{path}", headers=headers, params=params, timeout=30.0)

            if response.status_code == 404:
                raise WhoopAPIError(404, f"Resource not found: {path}")
            if response.status_code == 429:
                raise WhoopAPIError(429, "Rate limit exceeded")
            if response.status_code >= 500:
                raise WhoopAPIError(response.status_code, "WHOOP server error")
            if response.status_code >= 400:
                raise WhoopAPIError(response.status_code, f"API Error {response.status_code}")

            response.raise_for_status()
            return response.json()
        
    async def get(self, path: str, params: dict = None) -> dict:
        try:
            return await self._request("GET", path, params)
        except WhoopReauthRequired as e:
            raise WhoopAPIError(401, str(e)) from e


    def _parse_iso_utc(self, value: str | None) -> datetime | None:
        if not value:
            return None
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _env_int_minutes(name: str, default: int) -> int:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return default
        try:
            return max(0, int(raw))
        except ValueError:
            return default

    def _quiet_update_window(self) -> timedelta:
        return timedelta(minutes=self._env_int_minutes("WHOOP_QUIET_UPDATE_MINUTES", 15))

    @staticmethod
    def _score_state(record: dict) -> str:
        return str(record.get("score_state") or "").strip().upper()

    def _require_scored(self, record: dict, *, label: str, reason: str, day: date):
        score_state = self._score_state(record)
        if score_state != "SCORED":
            display_state = score_state or "missing"
            raise WhoopDailyDataPendingError(
                reason,
                f"{label} score_state is {display_state}; waiting for SCORED for {day} IST",
            )

    def _require_quiet_update(self, record: dict, *, label: str, reason: str, day: date):
        updated_dt = self._parse_iso_utc(record.get("updated_at"))
        if not updated_dt:
            raise WhoopDailyDataPendingError(
                f"{reason}_updated_at_missing",
                f"{label} updated_at is missing for {day} IST",
            )
        elapsed = self._now_utc() - updated_dt
        quiet_window = self._quiet_update_window()
        if elapsed < quiet_window:
            remaining = max(0, int((quiet_window - elapsed).total_seconds() // 60) + 1)
            raise WhoopDailyDataPendingError(
                reason,
                f"{label} updated {elapsed.total_seconds() / 60:.1f} minutes ago; waiting ~{remaining} more minute(s) for WHOOP to settle",
            )

    def _to_ist_date(self, value: str | None) -> date | None:
        dt = self._parse_iso_utc(value)
        if not dt:
            return None
        return dt.astimezone(self.local_tz).date()

    def _to_utc_z(self, local_dt: datetime) -> str:
        return local_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    async def _fetch_cycles(self, start_local: datetime, end_local: datetime, limit: int) -> list[dict]:
        params = {
            "start": self._to_utc_z(start_local),
            "end": self._to_utc_z(end_local),
            "limit": limit,
        }
        response = await self.get("/v2/cycle", params)
        return response.get("records", [])

    async def _get_cycles_window(self, target_date: date) -> list[dict]:
        # Pull a wide enough UTC window, then filter with IST-local rules.
        start_local = datetime.combine(target_date - timedelta(days=2), time.min, tzinfo=self.local_tz)
        end_local = datetime.combine(
            target_date + timedelta(days=self.DEFAULT_CYCLE_LOOKAHEAD_DAYS),
            time.max,
            tzinfo=self.local_tz,
        )
        return await self._fetch_cycles(start_local, end_local, self.DEFAULT_CYCLE_FETCH_LIMIT)

    @staticmethod
    def _has_valid_strain_score(cycle: dict) -> bool:
        score = cycle.get("score", {}) if isinstance(cycle, dict) else {}
        raw = score.get("strain") if isinstance(score, dict) else None
        if raw is None:
            return False
        try:
            strain = float(raw)
        except (TypeError, ValueError):
            return False
        return math.isfinite(strain)

    @staticmethod
    def _cycle_sort_id(cycle: dict) -> int:
        try:
            return int(cycle.get("id"))
        except (TypeError, ValueError):
            return -1

    def _collect_completed_strain_candidates(self, cycles: list[dict], sleep_start: datetime) -> list[tuple[dict, datetime, datetime]]:
        candidates = []
        for cycle in cycles:
            end_dt = self._parse_iso_utc(cycle.get("end"))
            if not end_dt or end_dt > sleep_start:
                continue

            score_state = str(cycle.get("score_state") or "").strip().upper()
            if score_state != "SCORED":
                continue

            if not self._has_valid_strain_score(cycle):
                continue

            updated_dt = self._parse_iso_utc(cycle.get("updated_at")) or datetime.min.replace(tzinfo=timezone.utc)
            candidates.append((cycle, end_dt, updated_dt))
        return candidates

    def _merge_cycles_by_id(self, existing: dict[int | str, dict], cycles: list[dict]) -> dict[int | str, dict]:
        for cycle in cycles:
            cycle_id = cycle.get("id")
            key = cycle_id if cycle_id is not None else id(cycle)
            existing[key] = cycle
        return existing

    async def _get_prior_completed_strain_cycle_candidates(self, day: date, sleep_start: datetime) -> list[tuple[dict, datetime, datetime]]:
        cycles_by_id: dict[int | str, dict] = {}
        initial_cycles = await self._get_cycles_window(day)
        self._merge_cycles_by_id(cycles_by_id, initial_cycles)

        candidates = self._collect_completed_strain_candidates(list(cycles_by_id.values()), sleep_start)
        if candidates:
            return candidates

        for lookback_days in self.EXPANDED_CYCLE_LOOKBACK_DAYS:
            start_local = datetime.combine(day - timedelta(days=lookback_days), time.min, tzinfo=self.local_tz)
            end_local = datetime.combine(
                day + timedelta(days=self.DEFAULT_CYCLE_LOOKAHEAD_DAYS),
                time.max,
                tzinfo=self.local_tz,
            )
            expanded_cycles = await self._fetch_cycles(
                start_local,
                end_local,
                self.EXPANDED_CYCLE_FETCH_LIMIT,
            )
            self._merge_cycles_by_id(cycles_by_id, expanded_cycles)
            candidates = self._collect_completed_strain_candidates(list(cycles_by_id.values()), sleep_start)
            if candidates:
                return candidates

        return []

    async def get_prior_completed_strain_cycle(self, target_date: datetime = None, sleep_data: dict | None = None) -> dict:
        """
        Get strain attribution cycle for local target day D.

        We define "yesterday's strain" as the most recent completed, scored cycle
        that ended at or before the primary sleep start for the sleep ending on D.
        """
        day = (target_date.date() if isinstance(target_date, datetime) else target_date) or datetime.now(self.local_tz).date()
        sleep = sleep_data or await self.get_last_sleep(target_date)
        sleep_start = self._parse_iso_utc(sleep.get("start"))
        if not sleep_start:
            raise WhoopDailyDataPendingError("primary_sleep_start_missing", f"Primary sleep start missing for {day} IST")

        candidates = await self._get_prior_completed_strain_cycle_candidates(day, sleep_start)

        if not candidates:
            raise WhoopDailyDataPendingError(
                "completed_pre_sleep_cycle_missing",
                f"No completed strain cycle found before primary sleep start for {day} IST",
            )

        candidates.sort(
            key=lambda item: (
                item[1],
                item[2],
                self._cycle_sort_id(item[0]),
            ),
            reverse=True,
        )
        return candidates[0][0]

    async def get_yesterday_cycle(self, target_date: datetime = None) -> dict:
        """Compatibility shim for older callers expecting the previous helper name."""
        return await self.get_prior_completed_strain_cycle(target_date)

    async def get_today_recovery(self, target_date: datetime = None, sleep_data: dict | None = None) -> dict:
        """
        Get recovery for local target day D:
        choose recovery where updated_at_ist == D and, when available,
        recovery.sleep_id matches the selected primary sleep.
        """
        day = (target_date.date() if isinstance(target_date, datetime) else target_date) or datetime.now(self.local_tz).date()
        cycles = await self._get_cycles_window(day)
        if not cycles:
            raise WhoopDailyDataPendingError("recovery_cycle_window_empty", f"No cycle data around {day} for recovery lookup")

        expected_sleep_id = str(sleep_data.get("id")) if isinstance(sleep_data, dict) and sleep_data.get("id") else ""
        matches: list[tuple[dict, datetime]] = []
        sleep_mismatches: list[str] = []
        for cycle in cycles:
            cycle_id = cycle.get("id")
            if not cycle_id:
                continue
            try:
                recovery = await self.get(f"/v2/cycle/{cycle_id}/recovery")
            except WhoopAPIError as e:
                if e.status_code == 404:
                    continue
                raise
            updated_at = recovery.get("updated_at")
            if self._to_ist_date(updated_at) == day:
                updated_dt = self._parse_iso_utc(updated_at)
                if not updated_dt:
                    continue
                recovery_sleep_id = str(recovery.get("sleep_id") or "")
                if expected_sleep_id and recovery_sleep_id != expected_sleep_id:
                    sleep_mismatches.append(recovery_sleep_id or "<missing>")
                    continue
                matches.append((recovery, updated_dt))

        if not matches:
            if expected_sleep_id and sleep_mismatches:
                raise WhoopDailyDataPendingError(
                    "whoop_recovery_sleep_mismatch",
                    f"Recovery entries for {day} IST were linked to sleep_id(s) {sorted(set(sleep_mismatches))}, not selected primary sleep {expected_sleep_id}",
                )
            raise WhoopDailyDataPendingError("recovery_missing", f"No recovery entry found for {day} IST")

        matches.sort(key=lambda t: t[1], reverse=True)
        selected = matches[0][0]
        self._require_scored(selected, label="Recovery", reason="whoop_recovery_unscored", day=day)
        self._require_quiet_update(selected, label="Recovery", reason="whoop_recovery_still_updating", day=day)
        return selected

    async def get_last_sleep(self, target_date: datetime = None) -> dict:
        """
        Get local target day D sleep:
        choose primary sleep where end_ist == D.
        """
        day = (target_date.date() if isinstance(target_date, datetime) else target_date) or datetime.now(self.local_tz).date()
        start_local = datetime.combine(day - timedelta(days=2), time.min, tzinfo=self.local_tz)
        end_local = datetime.combine(day + timedelta(days=1), time.max, tzinfo=self.local_tz)
        params = {
            "start": self._to_utc_z(start_local),
            "end": self._to_utc_z(end_local),
            "limit": 25,
        }
        response = await self.get("/v2/activity/sleep", params)
        records = response.get("records", [])
        primary_sleeps = [s for s in records if not s.get("nap", False)]
        matches = [s for s in primary_sleeps if self._to_ist_date(s.get("end")) == day]
        if not matches:
            raise WhoopDailyDataPendingError("primary_sleep_missing", f"No primary sleep found ending on {day} IST")
        matches.sort(key=lambda s: self._parse_iso_utc(s.get("end")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        selected = matches[0]
        self._require_scored(selected, label="Primary sleep", reason="whoop_sleep_unscored", day=day)
        self._require_quiet_update(selected, label="Primary sleep", reason="whoop_sleep_still_updating", day=day)
        return selected
