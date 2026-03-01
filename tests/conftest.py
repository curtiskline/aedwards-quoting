"""Pytest configuration for local source resolution.

Ensures tests import code from this worktree's ``src/`` directory first,
even when the shared virtualenv has an editable install from another path.
"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_SRC = PROJECT_ROOT / "src"

if str(LOCAL_SRC) not in sys.path:
    sys.path.insert(0, str(LOCAL_SRC))
