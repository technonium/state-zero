"""
OpenRouter LLM Client for prompt generation.

This module provides a client for calling OpenRouter's API with Minimax.
It includes fallback support to Google Gemini 2.5 Pro.

Configuration:
- Model: minimax/minimax-m2.5
- Temperature: 1.0
- Thinking: enabled with 8000 token budget
- Max tokens: 12000
"""

import os
import time
import requests
from typing import Optional


class OpenRouterError(Exception):
    """Custom exception for OpenRouter API errors."""
    pass


class LLMProviderError(Exception):
    """Custom exception for LLM provider failures."""
    pass


class OpenRouterClient:
    """
    Client for OpenRouter API with Minimax.
    
    Supports thinking mode for enhanced reasoning capabilities.
    Falls back to Google Gemini 2.5 Pro on failure.
    """
    
    BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "minimax/minimax-m2.5"
    
    # Default generation parameters
    DEFAULT_TEMPERATURE = 1.0
    DEFAULT_MAX_TOKENS = 12000
    DEFAULT_THINKING_BUDGET = 8000
    EMPTY_CONTENT_RETRY_DELAYS = (2, 5)
    
    def __init__(
        self,
        api_key: str,
        model: str = None,
        temperature: float = None,
        max_tokens: int = None,
        thinking_budget: int = None,
        fallback_api_key: str = None
    ):
        """
        Initialize the OpenRouter client.
        
        Args:
            api_key: OpenRouter API key
            model: Model to use (default: minimax/minimax-m2.5)
            temperature: Temperature for generation (default: 1.0)
            max_tokens: Maximum tokens in response (default: 12000)
            thinking_budget: Thinking token budget (default: 8000)
            fallback_api_key: Google API key for fallback (optional)
        """
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.temperature = temperature or self.DEFAULT_TEMPERATURE
        self.max_tokens = max_tokens or self.DEFAULT_MAX_TOKENS
        self.thinking_budget = thinking_budget or self.DEFAULT_THINKING_BUDGET
        self.fallback_api_key = fallback_api_key
        
    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        Generate text using OpenRouter API.
        
        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt
            
        Returns:
            Generated text response
            
        Raises:
            OpenRouterError: If API call fails
            LLMProviderError: If both primary and fallback fail
        """
        try:
            return self._call_openrouter(prompt, system_prompt)
        except OpenRouterError as e:
            print(f"⚠️  OpenRouter failed: {e}")
            if self.fallback_api_key:
                print("🔄 Attempting fallback to Google Gemini 2.5 Pro...")
                try:
                    return self._call_google_gemini(prompt, self.fallback_api_key)
                except Exception as fallback_error:
                    print(f"❌ Fallback also failed: {fallback_error}")
                    raise LLMProviderError(
                        f"Both OpenRouter and Google Gemini failed. "
                        f"OpenRouter: {e}, Fallback: {fallback_error}"
                    ) from fallback_error
            raise
    
    def _call_openrouter(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        Call OpenRouter API with Minimax.
        
        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt
            
        Returns:
            Generated text response
            
        Raises:
            OpenRouterError: If API call fails
        """
        url = f"{self.BASE_URL}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://dasha-yaml.local",
            "X-Title": "State Zero Pipeline"
        }
        
        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        # Build payload with thinking support
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "thinking": {
                "type": "enabled",
                "budget_tokens": self.thinking_budget
            }
        }
        
        print(f"🧠 Calling OpenRouter with {self.model}")
        print(f"   Temperature: {self.temperature}")
        print(f"   Max tokens: {self.max_tokens}")
        print(f"   Thinking budget: {self.thinking_budget}")
        print(f"   Prompt length: {len(prompt)} chars")
        
        total_attempts = len(self.EMPTY_CONTENT_RETRY_DELAYS) + 1
        for attempt in range(total_attempts):
            response = self._post_openrouter_request(url, headers, payload)
            data = response.json()

            if "choices" not in data or not data["choices"]:
                raise OpenRouterError(f"No choices in response. Keys: {list(data.keys())}")

            choice = data["choices"][0]
            message = choice.get("message", {})

            reasoning = message.get("reasoning") or message.get("thinking") or ""
            if isinstance(reasoning, str) and reasoning:
                print(f"   💭 Reasoning: {len(reasoning)} chars")

            content = self._extract_message_content(message)
            if content:
                print(f"   ✅ Response: {len(content)} chars")
                return content

            if attempt < len(self.EMPTY_CONTENT_RETRY_DELAYS):
                wait = self.EMPTY_CONTENT_RETRY_DELAYS[attempt]
                print(
                    f"⚠️  OpenRouter returned empty content — retrying in {wait}s "
                    f"(attempt {attempt + 2}/{total_attempts})..."
                )
                time.sleep(wait)
                continue

            raise OpenRouterError(f"No content in response message after {total_attempts} attempts")

    def _post_openrouter_request(self, url: str, headers: dict, payload: dict) -> requests.Response:
        """Execute a single OpenRouter request, including 429 retry handling."""
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=120)
            response.raise_for_status()
        except requests.exceptions.Timeout:
            raise OpenRouterError("Request timed out after 120 seconds")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                try:
                    retry_after = int(e.response.headers.get("Retry-After", 10))
                except (ValueError, TypeError):
                    retry_after = 10
                print(f"⏳ OpenRouter 429 — retrying in {retry_after}s...")
                time.sleep(retry_after)
                try:
                    response = requests.post(url, headers=headers, json=payload, timeout=120)
                    response.raise_for_status()
                except requests.exceptions.RequestException as retry_e:
                    raise OpenRouterError(f"Retry after 429 also failed: {retry_e}")
            else:
                error_text = e.response.text[:500] if e.response else str(e)
                raise OpenRouterError(f"HTTP {e.response.status_code}: {error_text}") if e.response else OpenRouterError(str(e))
        except requests.exceptions.RequestException as e:
            raise OpenRouterError(f"Request failed: {e}")

        return response

    def _extract_message_content(self, message: dict) -> str:
        """Extract final answer text from OpenRouter's message.content field."""
        content = message.get("content")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, str) and block.strip():
                    text_parts.append(block.strip())
                    continue

                if not isinstance(block, dict):
                    continue

                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    text_parts.append(text.strip())
                    continue

                nested_content = block.get("content")
                if isinstance(nested_content, str) and nested_content.strip():
                    text_parts.append(nested_content.strip())

            return "\n".join(text_parts).strip()

        return ""
    
    def _call_google_gemini(self, prompt: str, api_key: str) -> str:
        """
        Fallback to Google Gemini 2.5 Pro.
        
        Args:
            prompt: The user prompt
            api_key: Google API key
            
        Returns:
            Generated text response
        """
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent"
        
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json"
        }
        
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt}
                    ]
                }
            ]
        }
        
        print("🔄 Using Google Gemini 2.5 Pro as fallback")
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=90)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise LLMProviderError(f"Gemini HTTP {e.response.status_code}: {e.response.text[:500]}") if e.response else LLMProviderError(str(e))
        except requests.exceptions.Timeout:
            raise LLMProviderError("Gemini request timed out")
        except requests.exceptions.RequestException as e:
            raise LLMProviderError(f"Gemini request failed: {e}")
        
        data = response.json()
        
        candidates = data.get('candidates', [])
        if not candidates:
            raise LLMProviderError("No candidates in Gemini response")
        
        parts = candidates[0].get('content', {}).get('parts', [])
        texts = [part.get('text', '') for part in parts if part.get('text')]
        
        if not texts:
            raise LLMProviderError("No text parts in Gemini response")
        
        result = "\n".join(texts).strip()
        print(f"   ✅ Fallback response: {len(result)} chars")
        return result


def create_llm_client(
    openrouter_api_key: str = None,
    google_api_key: str = None,
    model: str = None,
    temperature: float = None,
    max_tokens: int = None,
    thinking_budget: int = None
) -> OpenRouterClient:
    """
    Factory function to create an LLM client with proper environment variable fallbacks.
    
    Args:
        openrouter_api_key: OpenRouter API key (or env var OPENROUTER_API_KEY)
        google_api_key: Google API key for fallback (or env var GOOGLE_API_KEY_PRIMARY)
        model: Model to use (default: minimax/minimax-m2.5)
        temperature: Temperature setting (default: 1.0)
        max_tokens: Max tokens (default: 12000)
        thinking_budget: Thinking budget (default: 8000)
        
    Returns:
        Configured OpenRouterClient instance
    """
    # Get API keys from environment if not provided
    openrouter_key = openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    google_key = google_api_key or os.getenv("GOOGLE_API_KEY_PRIMARY", "")
    
    if not openrouter_key:
        raise ValueError(
            "OpenRouter API key is required. "
            "Set OPENROUTER_API_KEY environment variable or pass directly."
        )
    
    print(f"🤖 Creating LLM Client")
    print(f"   Primary: OpenRouter ({model or 'minimax/minimax-m2.5'})")
    print(f"   Fallback: {'Google Gemini 2.5 Pro' if google_key else 'None'}")
    
    return OpenRouterClient(
        api_key=openrouter_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking_budget=thinking_budget,
        fallback_api_key=google_key if google_key else None
    )
