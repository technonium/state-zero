import os
import sys
import time
import argparse
import requests
from typing import Optional, Tuple

# Import token manager - works both as script and module
try:
    from .instagram_token_manager import InstagramTokenManager, get_instagram_token_manager
except ImportError:
    from instagram_token_manager import InstagramTokenManager, get_instagram_token_manager


class InstagramPublishResult:
    """Result object for Instagram publish operations."""
    
    def __init__(self, post_id: str, permalink: Optional[str] = None):
        self.post_id = post_id
        self.permalink = permalink
    
    def to_dict(self) -> dict:
        return {
            'post_id': self.post_id,
            'permalink': self.permalink
        }


class InstagramPoster:
    # Non-retryable error codes (per Instagram API documentation)
    NON_RETRYABLE_CODES = frozenset({10, 100, 190}) | set(range(200, 300))
    REQUEST_TIMEOUT = (10, 60)
    POLL_INTERVAL_SECONDS = 10
    POLL_MAX_BACKOFF_SECONDS = 60
    
    def __init__(self, access_token: str = None, user_id: str = None):
        # Use token manager to get valid token (handles refresh automatically)
        self.token_manager = get_instagram_token_manager()
        
        # If explicit tokens provided, use them (for backwards compatibility)
        # Otherwise, get from token manager
        if access_token and access_token != 'mock':
            self.access_token = access_token
        else:
            # Get fresh token from manager (may trigger refresh if needed)
            self.access_token = self.token_manager.get_valid_token()
        
        if user_id:
            self.user_id = user_id
        else:
            self.user_id = self.token_manager.get_user_id()
            
        self.base_url = "https://graph.facebook.com/v21.0"
        self.mock_mode = not self.access_token or self.access_token == 'mock'

    def _is_transient_error(self, response_data: dict) -> Tuple[bool, str]:
        """
        Determine if an Instagram API error is transient and safe to retry.
        
        Args:
            response_data: The JSON response from Instagram API
            
        Returns:
            Tuple of (should_retry: bool, error_summary: str)
        """
        # Check for HTTP status codes that indicate transient errors
        # (This check is for the response object, not the JSON body)
        
        # Check for error object in response
        error = response_data.get('error', {})
        if not error:
            return False, "No error object in response"
        
        error_code = error.get('code')
        is_transient = error.get('is_transient', False)
        error_message = error.get('message', 'Unknown error')
        
        # Build error summary
        error_summary = f"Code: {error_code}, Message: {error_message}, Is Transient: {is_transient}"
        
        # Non-retryable codes - return immediately without retry
        if error_code in self.NON_RETRYABLE_CODES:
            return False, f"Non-retryable error code {error_code}: {error_summary}"
        
        # Transient errors are safe to retry
        if is_transient:
            return True, f"Transient error (will retry): {error_summary}"
        
        # Default: don't retry unknown errors
        return False, f"Unknown error (not retrying): {error_summary}"

    def _check_transient_from_response(self, response: requests.Response) -> Tuple[bool, str]:
        """
        Check if HTTP response indicates a transient error.
        
        Args:
            response: The requests Response object
            
        Returns:
            Tuple of (should_retry: bool, error_summary: str)
        """
        status_code = response.status_code
        
        # HTTP 429 (Rate Limit) - transient
        if status_code == 429:
            return True, f"HTTP {status_code}: Rate limited (transient)"
        
        # 5xx Server Errors - transient
        if 500 <= status_code < 600:
            return True, f"HTTP {status_code}: Server error (transient)"
        
        # Check JSON body for error details
        try:
            response_data = response.json()
            if 'error' in response_data:
                return self._is_transient_error(response_data)
        except Exception:
            pass
        
        return False, f"HTTP {status_code}: Non-transient"

    def _get_retry_after_seconds(self, response: requests.Response) -> Optional[int]:
        """Return Retry-After delay when present and parseable."""
        retry_after = response.headers.get("Retry-After")
        if not retry_after:
            return None
        try:
            return max(1, int(retry_after))
        except (TypeError, ValueError):
            return None

    def _get_poll_retry_delay(self, transient_errors: int, response: Optional[requests.Response] = None) -> int:
        """
        Back off polling on transient failures without failing fast.

        Polling should be bounded by total elapsed time, not by a tiny number of
        transient blips. Honor Retry-After when Meta provides it; otherwise use
        capped exponential backoff from the normal poll interval.
        """
        retry_after = self._get_retry_after_seconds(response) if response is not None else None
        if retry_after is not None:
            return min(retry_after, self.POLL_MAX_BACKOFF_SECONDS)
        return min(
            self.POLL_INTERVAL_SECONDS * (2 ** max(0, transient_errors - 1)),
            self.POLL_MAX_BACKOFF_SECONDS,
        )

    def create_media_container(self, video_url: str, cover_url: str, caption: str) -> str:
        """Step 14a: Create media container with retry logic"""
        if self.mock_mode:
            print(f"Mocking IG Container creation. Video URL: {video_url}")
            return "mock_creation_id_123"

        url = f"{self.base_url}/{self.user_id}/media"
        share_to_feed = (os.getenv("INSTAGRAM_SHARE_TO_FEED", "false") or "false").strip().lower()
        if share_to_feed not in {"true", "false"}:
            share_to_feed = "false"
        payload = {
            'media_type': 'REELS',
            'video_url': video_url,
            'cover_url': cover_url,
            'caption': caption,
            'share_to_feed': share_to_feed,
            'access_token': self.access_token
        }
        
        # Retry configuration: 3 attempts with exponential backoff
        max_attempts = 3
        backoff_seconds = [5, 15, 30]  # 5s -> 15s -> 30s
        
        for attempt in range(max_attempts):
            try:
                response = requests.post(url, data=payload, timeout=self.REQUEST_TIMEOUT)
                response_data = response.json()
                
                if 'id' in response_data:
                    print(f"✅ Container created with ID: {response_data['id']}")
                    return response_data['id']
                
                # Check if this is a transient error
                should_retry, error_summary = self._check_transient_from_response(response)
                
                if should_retry and attempt < max_attempts - 1:
                    wait_time = backoff_seconds[attempt]
                    print(f"⚠ Transient error creating container: {error_summary}")
                    print(f"▶ Retrying in {wait_time}s... (attempt {attempt + 1}/{max_attempts})")
                    time.sleep(wait_time)
                    continue
                
                # Non-transient or exhausted retries
                error_msg = f"Failed to create container: {response_data}"
                print(f"❌ {error_msg}")
                raise RuntimeError(error_msg)
                
            except requests.exceptions.RequestException as e:
                if attempt < max_attempts - 1:
                    wait_time = backoff_seconds[attempt]
                    print(f"⚠ Network error creating container: {e}")
                    print(f"▶ Retrying in {wait_time}s... (attempt {attempt + 1}/{max_attempts})")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(f"Failed to create container after {max_attempts} attempts: {e}")

    def poll_processing_status(self, creation_id: str, max_polls=30) -> bool:
        """Step 14b: Poll for processing completion"""
        if self.mock_mode:
            print(f"Mocking IG Polling for {creation_id}...")
            time.sleep(1)
            return True

        url = f"{self.base_url}/{creation_id}?fields=status_code&access_token={self.access_token}"
        max_duration_seconds = max_polls * self.POLL_INTERVAL_SECONDS
        deadline = time.monotonic() + max_duration_seconds
        polls = 0
        transient_errors = 0

        while polls < max_polls and time.monotonic() < deadline:
            print(f"▶ Polling status (Attempt {polls+1}/{max_polls})...")
            try:
                response = requests.get(url, timeout=self.REQUEST_TIMEOUT)
            except requests.exceptions.RequestException as e:
                transient_errors += 1
                polls += 1
                delay = self._get_poll_retry_delay(transient_errors)
                print(
                    "⚠ Transient network error while polling status "
                    f"(streak {transient_errors}): {e}"
                )
                if polls >= max_polls:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(delay, remaining))
                continue

            polls += 1
            should_retry, error_summary = self._check_transient_from_response(response)
            if should_retry:
                transient_errors += 1
                delay = self._get_poll_retry_delay(transient_errors, response)
                print(
                    "⚠ Transient API status while polling "
                    f"(streak {transient_errors}): {error_summary}"
                )
                if polls >= max_polls:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(delay, remaining))
                continue

            data = response.json()
            status = data.get('status_code', 'UNKNOWN')
            transient_errors = 0

            if status == 'FINISHED':
                print(f"✅ Processing FINISHED for {creation_id}")
                return True
            elif status == 'ERROR':
                # Print full error details if available
                error_data = data.get('error', {})
                if error_data:
                    error_message = error_data.get('message', 'Unknown error')
                    error_code = error_data.get('code', 'N/A')
                    print(f"❌ Processing ERROR for {creation_id}: Code={error_code}, Message={error_message}")
                else:
                    print(f"❌ Processing ERROR for {creation_id}")
                return False

            if status != 'IN_PROGRESS':
                print(f"⚠ Unexpected processing status for {creation_id}: {status}")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self.POLL_INTERVAL_SECONDS, remaining))
            
        print(f"❌ Polling timeout reached after {max_duration_seconds}s.")
        return False

    def publish_media(self, creation_id: str) -> str:
        """Step 14c: Publish media with retry logic"""
        if self.mock_mode:
            print(f"Mocking IG Publish. Creation ID: {creation_id}")
            return "mock_ig_post_456"

        url = f"{self.base_url}/{self.user_id}/media_publish"
        payload = {
            'creation_id': creation_id,
            'access_token': self.access_token
        }
        
        # Retry configuration: 3 attempts with exponential backoff
        max_attempts = 3
        backoff_seconds = [5, 15, 30]  # 5s -> 15s -> 30s
        
        for attempt in range(max_attempts):
            try:
                response = requests.post(url, data=payload, timeout=self.REQUEST_TIMEOUT)
                response_data = response.json()
                
                if 'id' in response_data:
                    print(f"✅ Reel published successfully: {response_data['id']}")
                    return response_data['id']
                
                # Check if this is a transient error
                should_retry, error_summary = self._check_transient_from_response(response)
                
                if should_retry and attempt < max_attempts - 1:
                    wait_time = backoff_seconds[attempt]
                    print(f"⚠ Transient error publishing: {error_summary}")
                    print(f"▶ Retrying in {wait_time}s... (attempt {attempt + 1}/{max_attempts})")
                    time.sleep(wait_time)
                    continue
                
                # Non-transient or exhausted retries
                error_msg = f"Failed to publish reel: {response_data}"
                print(f"❌ {error_msg}")
                raise RuntimeError(error_msg)
                
            except requests.exceptions.RequestException as e:
                if attempt < max_attempts - 1:
                    wait_time = backoff_seconds[attempt]
                    print(f"⚠ Network error publishing: {e}")
                    print(f"▶ Retrying in {wait_time}s... (attempt {attempt + 1}/{max_attempts})")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(f"Failed to publish after {max_attempts} attempts: {e}")
    
    def get_permalink(self, post_id: str, max_retries: int = 5) -> Optional[str]:
        """
        Fetch the public permalink for a published media item.
        
        Args:
            post_id: The Instagram media post ID
            max_retries: Number of retry attempts (permalink can lag)
        
        Returns:
            Public permalink URL or None if unavailable
        """
        if self.mock_mode:
            print(f"Mocking IG Permalink for {post_id}")
            return "https://instagram.com/p/mock_permalink"
        
        url = f"{self.base_url}/{post_id}"
        params = {
            'fields': 'permalink',
            'access_token': self.access_token
        }
        
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=self.REQUEST_TIMEOUT)
                data = response.json()
                
                if 'permalink' in data:
                    print(f"✅ Got permalink: {data['permalink']}")
                    return data['permalink']
                
                if attempt < max_retries - 1:
                    print(f"▶ Permalink not ready, retrying in 2s... ({attempt + 1}/{max_retries})")
                    time.sleep(2)
                else:
                    print(f"⚠ Permalink unavailable after {max_retries} attempts")
                    return None
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    print(f"⚠ Error fetching permalink: {e}")
                    return None
        
        return None
    
    def publish_and_get_result(self, video_url: str, cover_url: str, caption: str) -> InstagramPublishResult:
        """
        Complete publish flow and return result with permalink.
        
        Args:
            video_url: Public URL of the video
            cover_url: Public URL of the cover thumbnail
            caption: Instagram caption
        
        Returns:
            InstagramPublishResult with post_id and permalink
        """
        # Create container
        creation_id = self.create_media_container(video_url, cover_url, caption)
        
        # Wait for processing
        if not self.poll_processing_status(creation_id):
            raise RuntimeError("Media processing failed on Instagram side")
        
        # Publish
        post_id = self.publish_media(creation_id)
        
        # Get permalink
        permalink = self.get_permalink(post_id)
        
        return InstagramPublishResult(post_id=post_id, permalink=permalink)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--container', action='store_true', help='Test creating container')
    args = parser.parse_args()

    # Use token manager - it handles refresh automatically
    token_manager = get_instagram_token_manager()
    access_token = token_manager.get_valid_token()
    user_id = token_manager.get_user_id()
    
    poster = InstagramPoster(access_token, user_id)
    
    if args.container:
        creation_id = poster.create_media_container(
            "https://mock.com/video.mp4", 
            "https://mock.com/thumb.jpg", 
            "Test caption"
        )
        if poster.poll_processing_status(creation_id):
            post_id = poster.publish_media(creation_id)
            print(f"Published Post ID: {post_id}")

if __name__ == '__main__':
    main()
