import base64
import json
import os
import sys
from pathlib import Path

import requests

# Add src/scripts to path to import utils
sys.path.insert(0, str(Path(__file__).parent))
from utils import get_project_root

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(dotenv_path=get_project_root() / '.env')

from google_key_router import GoogleAPIError, GoogleKeyRouter


class GoogleImageClient:
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        primary = os.getenv("GOOGLE_API_KEY_PRIMARY", "")
        fallback = os.getenv("GOOGLE_API_KEY_FALLBACK", "")
        self.router = GoogleKeyRouter(primary, fallback)
        self.model = os.getenv("GOOGLE_IMAGE_MODEL", "gemini-3.1-flash-image-preview")

    @staticmethod
    def _build_prompt_from_json(prompt_json: dict) -> str:
        # Keep it deterministic and complete: pass the structured JSON as text.
        return json.dumps(prompt_json, ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _extract_image_b64(response_json: dict) -> str:
        candidates = response_json.get("candidates", [])
        for candidate in candidates:
            parts = candidate.get("content", {}).get("parts", [])
            for part in parts:
                inline = part.get("inlineData") or part.get("inline_data") or {}
                mime = (inline.get("mimeType") or inline.get("mime_type") or "").lower()
                data = inline.get("data")
                if data and ("image/" in mime or not mime):
                    return data
        raise GoogleAPIError(500, "No image bytes found in generateContent response")

    def generate_from_json(self, prompt_json: dict, output_path: Path) -> Path:
        prompt_text = self._build_prompt_from_json(prompt_json)
        url = f"{self.BASE_URL}/models/{self.model}:generateContent"

        payload = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {
                    "aspectRatio": "3:4",
                    "imageSize": "2K",
                },
            },
        }

        def _call(api_key: str, key_label: str):
            headers = {
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=90)
            if resp.status_code >= 400:
                raise GoogleAPIError(resp.status_code, resp.text[:500])

            data = resp.json()
            img_b64 = self._extract_image_b64(data)
            image_bytes = base64.b64decode(img_b64)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(image_bytes)
            print(f"✅ Image generated via {key_label} key with model {self.model}: {output_path}")
            return output_path

        return self.router.execute_with_fallback(_call)
