import logging
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
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _to_ist_date(self, value: str | None) -> date | None:
        dt = self._parse_iso_utc(value)
        if not dt:
            return None
        return dt.astimezone(self.local_tz).date()

    def _to_utc_z(self, local_dt: datetime) -> str:
        return local_dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    async def _get_cycles_window(self, target_date: date) -> list[dict]:
        # Pull a wide enough UTC window, then filter with IST-local rules.
        start_local = datetime.combine(target_date - timedelta(days=2), time.min, tzinfo=self.local_tz)
        end_local = datetime.combine(target_date + timedelta(days=2), time.max, tzinfo=self.local_tz)
        params = {
            "start": self._to_utc_z(start_local),
            "end": self._to_utc_z(end_local),
            "limit": 25,
        }
        response = await self.get("/v2/cycle", params)
        return response.get("records", [])

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

        cycles = await self._get_cycles_window(day)
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

    async def get_today_recovery(self, target_date: datetime = None) -> dict:
        """
        Get recovery for local target day D:
        choose recovery where updated_at_ist == D.
        """
        day = (target_date.date() if isinstance(target_date, datetime) else target_date) or datetime.now(self.local_tz).date()
        cycles = await self._get_cycles_window(day)
        if not cycles:
            raise WhoopDailyDataPendingError("recovery_cycle_window_empty", f"No cycle data around {day} for recovery lookup")

        matches: list[tuple[dict, datetime]] = []
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
                if updated_dt:
                    matches.append((recovery, updated_dt))

        if not matches:
            raise WhoopDailyDataPendingError("recovery_missing", f"No recovery entry found for {day} IST")

        matches.sort(key=lambda t: t[1], reverse=True)
        return matches[0][0]

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
        return matches[0]
