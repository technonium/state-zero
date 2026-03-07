import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import httpx
from whoop_token_manager import WHOOPTokenManager, WhoopReauthRequired

logger = logging.getLogger(__name__)

class WhoopAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"WHOOP API Error ({status_code}): {message}")

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

    async def get_yesterday_cycle(self, target_date: datetime = None) -> dict:
        """
        Get cycle for strain attribution:
        for local target day D, use cycle with start_ist == D-1.
        """
        day = (target_date.date() if isinstance(target_date, datetime) else target_date) or datetime.now(self.local_tz).date()
        cycles = await self._get_cycles_window(day)
        target_start_day = day - timedelta(days=1)
        candidates = [c for c in cycles if self._to_ist_date(c.get("start")) == target_start_day]
        if not candidates:
            raise WhoopAPIError(404, f"No strain cycle found with start date {target_start_day} IST")
        # Pick the latest cycle that started on D-1 IST.
        candidates.sort(key=lambda c: self._parse_iso_utc(c.get("start")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return candidates[0]

    async def get_today_recovery(self, target_date: datetime = None) -> dict:
        """
        Get recovery for local target day D:
        choose recovery where updated_at_ist == D.
        """
        day = (target_date.date() if isinstance(target_date, datetime) else target_date) or datetime.now(self.local_tz).date()
        cycles = await self._get_cycles_window(day)
        if not cycles:
            raise WhoopAPIError(404, f"No cycle data around {day} for recovery lookup")

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
            raise WhoopAPIError(404, f"No recovery entry found for {day} IST")

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
            raise WhoopAPIError(404, f"No primary sleep found ending on {day} IST")
        matches.sort(key=lambda s: self._parse_iso_utc(s.get("end")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return matches[0]
