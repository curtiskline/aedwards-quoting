"""Tests for CLI provider resolution behavior."""

from pathlib import Path

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


def test_load_environment_prefers_worktree_then_shared_then_home(monkeypatch, tmp_path):
    fake_file = (
        tmp_path
        / "allenedwards"
        / "worktrees"
        / "local-pipeline-test"
        / "src"
        / "allenedwards"
        / "cli.py"
    )
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_text("# test")

    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / ".env").write_text("EMAIL_PROVIDER=o365\n")
    (tmp_path / "allenedwards" / ".env").write_text("EMAIL_PROVIDER=gmail\n")
    (tmp_path / "allenedwards" / "worktrees" / "local-pipeline-test" / ".env").write_text(
        "EMAIL_PROVIDER=minimax\n"
    )

    monkeypatch.setattr(cli, "__file__", str(fake_file))
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    monkeypatch.setattr(cli, "_ENV_LOADED", False)

    cli.load_environment()

    assert cli.os.environ.get("EMAIL_PROVIDER") == "minimax"


def test_load_environment_does_not_override_existing_env(monkeypatch, tmp_path):
    fake_file = (
        tmp_path
        / "allenedwards"
        / "worktrees"
        / "local-pipeline-test"
        / "src"
        / "allenedwards"
        / "cli.py"
    )
    fake_file.parent.mkdir(parents=True, exist_ok=True)
    fake_file.write_text("# test")

    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / ".env").write_text("EMAIL_PROVIDER=o365\n")
    (tmp_path / "allenedwards" / ".env").write_text("EMAIL_PROVIDER=gmail\n")
    (tmp_path / "allenedwards" / "worktrees" / "local-pipeline-test" / ".env").write_text(
        "EMAIL_PROVIDER=minimax\n"
    )

    monkeypatch.setattr(cli, "__file__", str(fake_file))
    monkeypatch.setattr(Path, "home", lambda: home_dir)
    monkeypatch.setenv("EMAIL_PROVIDER", "gmail")
    monkeypatch.setattr(cli, "_ENV_LOADED", False)

    cli.load_environment()

    assert cli.os.environ.get("EMAIL_PROVIDER") == "gmail"
