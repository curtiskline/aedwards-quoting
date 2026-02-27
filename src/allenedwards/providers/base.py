"""Base LLM provider interface."""

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def complete(self, prompt: str, system: str | None = None) -> str:
        """Generate a completion for the given prompt.

        Args:
            prompt: The user prompt/input
            system: Optional system prompt

        Returns:
            The model's text response
        """
        pass

    @abstractmethod
    def complete_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        """Generate a JSON completion for the given prompt.

        Args:
            prompt: The user prompt/input
            system: Optional system prompt

        Returns:
            Parsed JSON response from the model
        """
        pass
