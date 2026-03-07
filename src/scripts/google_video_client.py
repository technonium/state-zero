import base64
import io
import os
import sys
import time
from pathlib import Path

# Add src/scripts to path to import utils
sys.path.insert(0, str(Path(__file__).parent))
from utils import get_project_root

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(dotenv_path=get_project_root() / '.env')

import requests
from PIL import Image

from google_key_router import GoogleAPIError, GoogleKeyRouter


class GoogleVideoClient:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        primary = os.getenv("GOOGLE_API_KEY_PRIMARY", "")
        fallback = os.getenv("GOOGLE_API_KEY_FALLBACK", "")
        self.router = GoogleKeyRouter(primary, fallback)
        self.default_model = os.getenv("GOOGLE_VIDEO_MODEL", "veo-3.1-fast-generate-preview")
        self.timeout_seconds = int(os.getenv("GOOGLE_VIDEO_TIMEOUT_SECONDS", "900"))
        self.poll_seconds = int(os.getenv("GOOGLE_VIDEO_POLL_SECONDS", "10"))

    @staticmethod
    def _extract_operation_name(start_json: dict) -> str:
        name = start_json.get("name", "")
        if not name:
            raise GoogleAPIError(500, "Missing operation name from video start response")
        return name

    @staticmethod
    def _extract_video_uri(done_json: dict) -> str:
        response_obj = done_json.get("response", {})
        if not isinstance(response_obj, dict):
            raise GoogleAPIError(500, "Malformed operation response")

        # Gemini REST docs commonly return:
        # response.generateVideoResponse.generatedSamples[0].video.uri
        gvr = response_obj.get("generateVideoResponse", {})
        if isinstance(gvr, dict):
            samples = gvr.get("generatedSamples", [])
            for sample in samples:
                if isinstance(sample, dict):
                    video = sample.get("video", {})
                    if isinstance(video, dict):
                        uri = video.get("uri") or video.get("url")
                        if uri:
                            return uri

        # Keep compatibility with SDK-shaped operation responses.
        for key in ("generatedVideos", "videos"):
            items = response_obj.get(key, [])
            for item in items:
                if isinstance(item, dict):
                    video_obj = item.get("video", {})
                    if isinstance(video_obj, dict):
                        uri = video_obj.get("uri") or video_obj.get("url")
                        if uri:
                            return uri
                    uri = item.get("uri") or item.get("url")
                    if uri:
                        return uri
        raise GoogleAPIError(500, "Video URI not found in completed operation response")

    def _download_video(self, uri: str, api_key: str, output_path: Path):
        headers = {"x-goog-api-key": api_key}
        resp = requests.get(uri, headers=headers, timeout=180)
        if resp.status_code >= 400:
            raise GoogleAPIError(resp.status_code, f"Video download failed: {resp.text[:500]}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(resp.content)

    @staticmethod
    def _to_letterboxed_png_bytes(image_path: Path, target_width: int = 1080, target_height: int = 1920) -> tuple[bytes, tuple[int, int]]:
        with Image.open(image_path) as source:
            source = source.convert("RGB")
            src_width, src_height = source.size

            scale = min(target_width / src_width, target_height / src_height)
            resized_width = max(1, int(round(src_width * scale)))
            resized_height = max(1, int(round(src_height * scale)))

            resized = source.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
            canvas = Image.new("RGB", (target_width, target_height), (0, 0, 0))
            offset_x = (target_width - resized_width) // 2
            offset_y = (target_height - resized_height) // 2
            canvas.paste(resized, (offset_x, offset_y))

            out = io.BytesIO()
            canvas.save(out, format="PNG")
            return out.getvalue(), (src_width, src_height)

    @staticmethod
    def _build_actionable_error(error_text: str) -> str:
        lowered = error_text.lower()
        unsupported_markers = (
            "isn't supported by this model",
            "not supported by this model",
            "unsupported",
            "invalid argument",
        )
        if any(marker in lowered for marker in unsupported_markers):
            return (
                f"{error_text}\n"
                "Image-conditioned video request was rejected by the API schema/access for this key/project. "
                "Text-to-video fallback is intentionally disabled."
            )
        return error_text

    def generate_from_image(self, prompt_text: str, image_path: Path, output_path: Path) -> Path:
        """
        Generate video from image using VEO 3.1 Fast.

        Image acts as first frame. No text-to-video fallback.
        """
        framed_png_bytes, source_size = self._to_letterboxed_png_bytes(image_path, target_width=1080, target_height=1920)
        image_b64 = base64.b64encode(framed_png_bytes).decode("utf-8")

        def _call(api_key: str, key_label: str):
            model = self.default_model
            url = f"{self.BASE_URL}/models/{model}:predictLongRunning"

            headers = {
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            }

            payload = {
                "instances": [
                    {
                        "prompt": prompt_text,
                        "image": {
                            "mimeType": "image/png",
                            "bytesBase64Encoded": image_b64,
                        },
                    }
                ],
                "parameters": {
                    "aspectRatio": "9:16",
                    "resolution": "1080p",
                    "durationSeconds": 8,
                },
            }

            print(f"🎬 Starting video generation with {model}...")
            print(f"   Prompt: {prompt_text[:80]}...")
            print(f"   Image: {image_path.name}")
            print(f"   Source size: {source_size[0]}x{source_size[1]}")
            print("   Letterbox canvas: 1080x1920 (black bars, no crop)")
            print("   Payload image mode: bytesBase64Encoded")

            start_resp = requests.post(url, headers=headers, json=payload, timeout=120)

            if start_resp.status_code >= 400:
                error_msg = self._build_actionable_error(start_resp.text[:500])
                print(f"❌ Video generation failed with {start_resp.status_code}")
                print(f"   Error: {error_msg}")
                raise GoogleAPIError(start_resp.status_code, error_msg)

            op_name = self._extract_operation_name(start_resp.json())
            print(f"✓ Operation started: {op_name}")

            started_at = time.time()
            poll_count = 0
            while True:
                elapsed = time.time() - started_at
                if elapsed > self.timeout_seconds:
                    raise GoogleAPIError(504, f"Video generation timed out after {self.timeout_seconds}s")

                op_url = f"{self.BASE_URL}/{op_name}"
                op_resp = requests.get(op_url, headers=headers, timeout=60)
                if op_resp.status_code >= 400:
                    raise GoogleAPIError(op_resp.status_code, op_resp.text[:500])
                op_json = op_resp.json()

                if op_json.get("error"):
                    err = op_json["error"]
                    msg = err.get("message", "Unknown operation error")
                    code = err.get("code", 500)
                    print(f"❌ Operation failed: {msg}")
                    raise GoogleAPIError(code, msg)

                if op_json.get("done") is True:
                    print(f"✓ Video generation completed after {int(elapsed)}s")
                    video_uri = self._extract_video_uri(op_json)
                    self._download_video(video_uri, api_key, output_path)
                    print(f"✅ Video generated via {key_label} key with {model}")
                    print(f"   Output: {output_path}")
                    return output_path

                poll_count += 1
                if poll_count % 6 == 0:
                    print(f"   Still processing... ({int(elapsed)}s elapsed)")

                time.sleep(self.poll_seconds)

        return self.router.execute_with_fallback(_call)
