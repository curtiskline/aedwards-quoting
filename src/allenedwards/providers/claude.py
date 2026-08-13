"""Claude API LLM provider."""

import json
import os
from typing import Any

import anthropic

from .base import LLMProvider, LLMResponseTruncated


class ClaudeProvider(LLMProvider):
    """Anthropic Claude API provider."""

    MODEL = "claude-sonnet-4-6"
    DEFAULT_MAX_TOKENS = 4096
    # Structured-JSON parses of multi-line RFQs routinely exceed 4096 output
    # tokens (an 11-line order overflowed in prod on 2026-08-13), so JSON
    # completions get a much larger budget, with one doubled retry on overflow.
    JSON_MAX_TOKENS = 16384

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.json_max_tokens = int(
            os.environ.get("CLAUDE_JSON_MAX_TOKENS", str(self.JSON_MAX_TOKENS))
        )

    def _create_message(
        self, prompt: str, system: str | None, max_tokens: int
    ) -> anthropic.types.Message:
        kwargs: dict[str, Any] = {
            "model": self.MODEL,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        return self.client.messages.create(**kwargs)

    def complete(self, prompt: str, system: str | None = None) -> str:
        message = self._create_message(prompt, system, self.DEFAULT_MAX_TOKENS)
        return message.content[0].text

    def complete_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        # Add instruction for JSON output
        json_prompt = f"{prompt}\n\nRespond with valid JSON only, no markdown code blocks."

        max_tokens = self.json_max_tokens
        for attempt in range(2):
            message = self._create_message(json_prompt, system, max_tokens)

            if message.stop_reason == "max_tokens":
                if attempt == 0:
                    max_tokens *= 2
                    continue
                raise LLMResponseTruncated(
                    f"LLM response truncated: stop_reason=max_tokens at "
                    f"max_tokens={max_tokens} (model={self.MODEL}); JSON output "
                    f"is incomplete and was not parsed"
                )

            # Strip any markdown code blocks if present
            text = message.content[0].text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                if attempt == 0:
                    continue
                raise
