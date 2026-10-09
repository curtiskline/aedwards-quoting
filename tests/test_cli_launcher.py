"""Regression tests for scripts/allenedwards, the PATH-installable launcher.

Spawned worktree agents invoke the bare ``allenedwards`` command advertised by
axon discover. That resolves (via a ~/.local/bin symlink) to this launcher,
which must run the CLI of the checkout the caller is standing in — not the
main checkout the worktree venv's editable install points at.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "scripts" / "allenedwards"


def run_launcher(*args, cwd=REPO_ROOT, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(LAUNCHER), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_launcher_is_executable():
    assert LAUNCHER.exists()
    assert os.access(LAUNCHER, os.X_OK)


def test_launcher_help_runs_the_cli():
    result = run_launcher("--help")
    assert result.returncode == 0, result.stderr
    assert "Allan Edwards RFQ-to-Quote CLI Tool" in result.stdout


def test_launcher_resolves_callers_checkout_over_its_own():
    # From inside this checkout the launcher must pick THIS checkout as root,
    # even if the launcher file itself lives elsewhere (symlink install).
    result = run_launcher(
        cwd=REPO_ROOT, env_extra={"ALLENEDWARDS_LAUNCHER_PRINT_ROOT": "1"}
    )
    assert result.returncode == 0, result.stderr
    lines = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert Path(lines["root"]).resolve() == REPO_ROOT


def test_launcher_falls_back_to_its_own_checkout_outside_any_repo(tmp_path):
    # tmp_path is outside any git checkout: the launcher must fall back to the
    # checkout the script lives in rather than failing or picking cwd.
    result = run_launcher(
        cwd=tmp_path, env_extra={"ALLENEDWARDS_LAUNCHER_PRINT_ROOT": "1"}
    )
    assert result.returncode == 0, result.stderr
    lines = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    assert Path(lines["root"]).resolve() == REPO_ROOT
