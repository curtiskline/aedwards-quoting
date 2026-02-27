"""MiniMax M2 LLM provider."""

import json
import os
from typing import Any

import httpx

from .base import LLMProvider


class MiniMaxProvider(LLMProvider):
    """MiniMax M2 API provider."""

    API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"
    MODEL = "MiniMax-Text-01"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY")
        if not self.api_key:
            raise ValueError("MINIMAX_API_KEY not set")

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

        with httpx.Client(timeout=120.0) as client:
            response = client.post(self.API_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        return data["choices"][0]["message"]["content"]

    def complete(self, prompt: str, system: str | None = None) -> str:
        return self._call_api(prompt, system, json_mode=False)

    def complete_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        response = self._call_api(prompt, system, json_mode=True)
        return json.loads(response)
