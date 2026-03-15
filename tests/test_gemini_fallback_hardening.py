import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "src/scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from openrouter_client import (
    GEMINI_MODEL,
    GEMINI_TIMEOUT_SECONDS,
    OpenRouterClient,
    OpenRouterError,
    call_google_gemini_generate_content,
)
from prompts import PromptOrchestrator


class _FakeGeminiResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class GeminiFallbackHardeningTests(unittest.TestCase):
    def test_shared_gemini_helper_sends_explicit_generation_config(self):
        fake_payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "explicit config response"}
                        ]
                    }
                }
            ]
        }

        with patch(
            "openrouter_client.requests.post",
            return_value=_FakeGeminiResponse(fake_payload),
        ) as post_mock:
            result = call_google_gemini_generate_content(
                prompt="test prompt",
                api_key="google-key",
                model=GEMINI_MODEL,
                temperature=1.0,
                max_output_tokens=12000,
                thinking_budget=8000,
                timeout=GEMINI_TIMEOUT_SECONDS,
            )

        self.assertEqual(result, "explicit config response")
        _, kwargs = post_mock.call_args
        self.assertIn(f"/models/{GEMINI_MODEL}:generateContent", kwargs["url"] if "url" in kwargs else post_mock.call_args.args[0])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "google-key")
        self.assertEqual(kwargs["timeout"], GEMINI_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["json"]["generationConfig"]["temperature"], 1.0)
        self.assertEqual(kwargs["json"]["generationConfig"]["maxOutputTokens"], 12000)
        self.assertEqual(
            kwargs["json"]["generationConfig"]["thinkingConfig"]["thinkingBudget"],
            8000,
        )

    def test_openrouter_fallback_uses_shared_gemini_helper(self):
        client = OpenRouterClient(
            api_key="openrouter-key",
            fallback_api_key="google-key",
            temperature=1.0,
            max_tokens=12000,
            thinking_budget=8000,
        )

        with patch.object(client, "_call_openrouter", side_effect=OpenRouterError("primary failed")):
            with patch(
                "openrouter_client.call_google_gemini_generate_content",
                return_value="fallback text",
            ) as gemini_mock:
                result = client.generate("prompt body")

        self.assertEqual(result, "fallback text")
        gemini_mock.assert_called_once_with(
            prompt="prompt body",
            api_key="google-key",
            model=GEMINI_MODEL,
            temperature=1.0,
            max_output_tokens=12000,
            thinking_budget=8000,
            timeout=GEMINI_TIMEOUT_SECONDS,
        )

    def test_direct_gemini_path_uses_shared_helper_with_explicit_limits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(
                os.environ,
                {
                    "STATE_ZERO_PRIVATE_ROOT": tmpdir,
                    "PIPELINE_DATE": "2026-03-15",
                    "OPENROUTER_API_KEY": "",
                },
                clear=False,
            ):
                orchestrator = PromptOrchestrator(llm_api_key="google-key", openrouter_api_key=None)

                with patch(
                    "prompts.call_google_gemini_generate_content",
                    return_value="direct gemini text",
                ) as gemini_mock:
                    result = orchestrator.call_llm("prompt body")

        self.assertEqual(result, "direct gemini text")
        gemini_mock.assert_called_once_with(
            prompt="prompt body",
            api_key="google-key",
            model=GEMINI_MODEL,
            temperature=1.0,
            max_output_tokens=12000,
            thinking_budget=8000,
            timeout=GEMINI_TIMEOUT_SECONDS,
        )

    def test_openrouter_defaults_remain_unchanged(self):
        client = OpenRouterClient(api_key="openrouter-key")
        self.assertEqual(client.max_tokens, 12000)
        self.assertEqual(client.thinking_budget, 8000)


if __name__ == "__main__":
    unittest.main()
