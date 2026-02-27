"""MiniMax M2 LLM provider using OpenAI-compatible API."""

import json
import os
from pathlib import Path
from typing import Any

import httpx

from .base import LLMProvider


def load_minimax_config() -> tuple[str | None, str]:
    """Load MiniMax API key and base URL from openclaw config or environment.

    Returns:
        Tuple of (api_key, base_url)
    """
    api_key = os.environ.get("MINIMAX_API_KEY")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1")

    # Try loading from openclaw config if not in environment
    if not api_key:
        config_path = Path.home() / ".openclaw" / "openclaw.json"
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = json.load(f)
                    # Look for MiniMax provider config
                    providers = config.get("providers", {})
                    minimax = providers.get("minimax", {})
                    api_key = minimax.get("api_key") or minimax.get("key")
                    base_url = minimax.get("base_url", base_url)
            except (json.JSONDecodeError, OSError):
                pass

    return api_key, base_url


class MiniMaxProvider(LLMProvider):
    """MiniMax M2 API provider using OpenAI-compatible completions API."""

    MODEL = "MiniMax-M2"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        loaded_key, loaded_url = load_minimax_config()
        self.api_key = api_key or loaded_key
        self.base_url = base_url or loaded_url

        if not self.api_key:
            raise ValueError(
                "MINIMAX_API_KEY not set. Set it in environment or ~/.openclaw/openclaw.json"
            )

    def _call_api(
        self, prompt: str, system: str | None = None, json_mode: bool = False
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.MODEL,
            "messages": messages,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        url = f"{self.base_url}/chat/completions"

        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        return data["choices"][0]["message"]["content"]

    def complete(self, prompt: str, system: str | None = None) -> str:
        return self._call_api(prompt, system, json_mode=False)

    def complete_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        response = self._call_api(prompt, system, json_mode=True)
        return json.loads(self._extract_json(response))

    def _extract_json(self, text: str) -> str:
        """Extract JSON from response, stripping <think> tags if present."""
        import re

        # Remove <think>...</think> blocks (MiniMax M2 includes reasoning)
        text = re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL)
        text = text.strip()

        # Handle markdown code blocks
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]

        return text.strip()
