import requests

from utils import env_bool

_RETRYABLE_4XX = frozenset({401, 403, 429})


class GoogleAPIError(Exception):
    """Error wrapper with HTTP status and response text."""

    def __init__(self, status_code: int | None, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(message)


class GoogleKeyRouter:
    """Two-key router: primary -> fallback with transient retry."""

    def __init__(self, primary_key: str, fallback_key: str):
        if not env_bool("GOOGLE_API_FALLBACK_ENABLED"):
            fallback_key = ""
        
        self.keys = [
            ("primary", (primary_key or "").strip()),
            ("fallback", (fallback_key or "").strip()),
        ]
        self.keys = [(label, key) for label, key in self.keys if key]
        if not self.keys:
            raise ValueError("No Google API keys configured")

    @staticmethod
    def _sanitize_body(text: str, limit: int = 400) -> str:
        return (text or "").strip().replace("\n", " ")[:limit]

    @staticmethod
    def _is_retryable_http(status_code: int | None) -> bool:
        if status_code is None:
            return False
        return status_code in _RETRYABLE_4XX or status_code >= 500

    @staticmethod
    def _is_non_retryable_client_error(status_code: int | None) -> bool:
        return status_code is not None and 400 <= status_code < 500 and status_code not in _RETRYABLE_4XX

    @staticmethod
    def _is_transient_exception(exc: Exception) -> bool:
        return isinstance(exc, (requests.Timeout, requests.ConnectionError))

    def execute_with_fallback(self, call_fn):
        """
        call_fn must be: fn(api_key: str, key_label: str) -> Any
        Retry once per key on transient exceptions.
        """
        last_error = None
        for key_label, api_key in self.keys:
            for attempt in range(2):
                try:
                    return call_fn(api_key, key_label)
                except GoogleAPIError as e:
                    last_error = e
                    if self._is_non_retryable_client_error(e.status_code):
                        raise
                    if self._is_retryable_http(e.status_code):
                        break
                    raise
                except Exception as e:
                    last_error = e
                    if self._is_transient_exception(e):
                        if attempt < 1:
                            continue
                        break
                    raise

        if isinstance(last_error, GoogleAPIError):
            raise GoogleAPIError(
                last_error.status_code,
                f"All Google keys exhausted. Last error ({last_error.status_code}): {last_error.message}",
            )
        if last_error is not None:
            raise RuntimeError(f"All Google keys exhausted. Last error: {last_error}")
        raise RuntimeError("All Google keys exhausted with unknown error")
