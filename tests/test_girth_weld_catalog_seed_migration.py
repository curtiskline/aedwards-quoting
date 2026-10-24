"""Migration 20261024_0001: seed per-set girth-weld range rows (task 530).

The ranges are read from the pricing_table at migration time rather than
hardcoded, so the catalog mirrors whatever per-set ranges the deployment
actually prices from (after 20261019_0003 those are 2-16 / 18-30 / 32-44).
The rows carry no part number: lines keep their generated per-size numbers.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CMD = [sys.executable, "-m", "alembic"]


def _alembic(db_path: Path, *args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PYTHONPATH"] = str(ROOT / "src")
    subprocess.run([*ALEMBIC_CMD, *args], check=True, cwd=ROOT, env=env)


def _girth_catalog_rows(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT part_number, is_active, CAST(diameter_min AS INT),"
        " CAST(diameter_max AS INT), description"
        " FROM product_catalog WHERE product_type = 'girth_weld'"
        " ORDER BY diameter_min"
    ).fetchall()


def test_seed_matches_live_pricing_ranges_and_downgrade_removes_them(tmp_path):
    db_path = tmp_path / "girth_seed.db"
    _alembic(db_path, "upgrade", "head")

    with sqlite3.connect(db_path) as conn:
        rows = _girth_catalog_rows(conn)
        assert [(r[2], r[3]) for r in rows] == [(2, 16), (18, 30), (32, 44)]
        assert all(r[0] is None for r in rows), "range rows must carry no part number"
        assert all(r[1] == 1 for r in rows)
        # The description must not contain a "<low>-<high>in" token, which
        # echo_requested_size would rewrite onto the line's frozen description.
        from app.catalog_resolution import echo_requested_size

        assert all(echo_requested_size(r[4], 10.75) == r[4] for r in rows)

        # Ranges mirror the pricing table the same database prices from.
        pricing = conn.execute(
            "SELECT key_fields FROM pricing_table WHERE product_type = 'girth_weld'"
        ).fetchall()
        assert len(pricing) == len(rows)

    _alembic(db_path, "downgrade", "-1")
    with sqlite3.connect(db_path) as conn:
        assert _girth_catalog_rows(conn) == []

    # Re-upgrading is idempotent: exactly one row per range, not duplicates.
    _alembic(db_path, "upgrade", "head")
    _alembic(db_path, "downgrade", "20261019_0003")
    _alembic(db_path, "upgrade", "head")
    with sqlite3.connect(db_path) as conn:
        assert len(_girth_catalog_rows(conn)) == 3
