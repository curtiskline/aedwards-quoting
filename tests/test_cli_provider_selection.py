"""Tests for CLI provider resolution behavior."""

from allenedwards import cli


def test_resolve_provider_name_explicit_provider_wins_over_api_key(monkeypatch):
    """Explicit LLM_PROVIDER must take precedence over ANTHROPIC_API_KEY."""
    monkeypatch.setattr(cli, "load_environment", lambda: None)
    monkeypatch.setenv("LLM_PROVIDER", "minimax")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "present")

    assert cli.resolve_provider_name() == "minimax"


def test_resolve_provider_name_defaults_to_claude_when_anthropic_key_present(monkeypatch):
    """Claude should be selected when provider is unset and Anthropic key exists."""
    monkeypatch.setattr(cli, "load_environment", lambda: None)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "present")

    assert cli.resolve_provider_name() == "claude"


def test_resolve_provider_name_defaults_to_minimax_without_provider_or_claude_key(monkeypatch):
    """MiniMax should be selected when no explicit provider or Anthropic key exists."""
    monkeypatch.setattr(cli, "load_environment", lambda: None)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert cli.resolve_provider_name() == "minimax"
