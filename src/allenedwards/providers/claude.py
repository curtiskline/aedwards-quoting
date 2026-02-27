"""Claude API LLM provider."""

import json
import os
from typing import Any

import anthropic

from .base import LLMProvider


class ClaudeProvider(LLMProvider):
    """Anthropic Claude API provider."""

    MODEL = "claude-sonnet-4-20250514"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=self.api_key)

    def complete(self, prompt: str, system: str | None = None) -> str:
        kwargs: dict[str, Any] = {
            "model": self.MODEL,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        message = self.client.messages.create(**kwargs)
        return message.content[0].text

    def complete_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        # Add instruction for JSON output
        json_prompt = f"{prompt}\n\nRespond with valid JSON only, no markdown code blocks."

        response = self.complete(json_prompt, system)

        # Strip any markdown code blocks if present
        text = response.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        return json.loads(text)
