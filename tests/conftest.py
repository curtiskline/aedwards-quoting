"""Pytest configuration for local source imports.

Ensure tests always exercise the current worktree code instead of any globally
or editable-installed copy from a different checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
