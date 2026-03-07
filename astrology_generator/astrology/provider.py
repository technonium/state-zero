from __future__ import annotations

import time
from datetime import date
from typing import Any

import requests

API_URL = "https://vedicrishi.in/api/vedicrishi"

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Origin": "https://vedicrishi.in",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
}

REFERERS = {
    "kp_details": "https://vedicrishi.in/kundli/kp-details",
    "current_vdasha_date": "https://vedicrishi.in/kundli/vimshottari-dasha",
}


class ProviderError(RuntimeError):
    """Raised when the upstream astrology provider fails."""


class VedicRishiExposedProvider:
    """Thin client for the currently exposed VedicRishi endpoint."""

    def __init__(self, timeout_seconds: int = 20, max_retries: int = 3) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "VedicRishiExposedProvider":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _post(self, api_name: str, user_data: dict[str, Any]) -> dict[str, Any]:
        payload = {"apiName": api_name, "userData": user_data}
        headers = {**BASE_HEADERS, "Referer": REFERERS[api_name]}

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.post(
                    API_URL,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise ProviderError(
                        f"{api_name} returned retryable status {response.status_code}"
                    )
                response.raise_for_status()
                data = response.json()
                if not data.get("status"):
                    raise ProviderError(data.get("msg") or f"{api_name} returned status=false")
                result = data.get("response")
                if not isinstance(result, dict):
                    raise ProviderError(f"{api_name} returned malformed response payload")
                return result
            except Exception as exc:  # pragma: no cover - network edge cases
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(min(2 * attempt, 5))

        raise ProviderError(f"{api_name} failed after {self.max_retries} attempts: {last_error}")

    def fetch_kp_details(self, user_data: dict[str, Any]) -> dict[str, Any]:
        return self._post("kp_details", user_data)

    def fetch_current_vdasha(self, user_data: dict[str, Any], query_date: date) -> dict[str, Any]:
        payload = {
            **user_data,
            "dasha_date": f"{query_date.month}-{query_date.day}-{query_date.year}",
        }
        return self._post("current_vdasha_date", payload)
