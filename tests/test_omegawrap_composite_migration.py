"""Round-trip test for migration 20260811_0001 (task 362).

The migration rewrites the product_type STRING 'omegawrap' -> 'composite' in
pricing_table and quote_line_item, leaving internal variant keys
(key_fields.key = 'omegawrap_carbon') untouched. We drive the real alembic
migration up/down over a DB seeded with both an omegawrap pricing row and an
omegawrap line item, and assert no such product_type survives the upgrade and
that downgrade restores it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_CMD = [sys.executable, "-m", "alembic"]

PREV_REV = "20260810_0003"
THIS_REV = "20260811_0001"


def _alembic_env(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _alembic(db_path: Path, *args: str) -> None:
    subprocess.run(
        [*ALEMBIC_CMD, *args],
        check=True,
        cwd=ROOT,
        env=_alembic_env(db_path),
    )


def _seed(conn: sqlite3.Connection) -> None:
    # Wrap pricing rows: product_type 'omegawrap', variant key preserved in key_fields.
    conn.executemany(
        "INSERT INTO pricing_table (product_type, key_fields, price, updated_at) "
        "VALUES (:pt, :kf, :price, '2026-08-11 00:00:00')",
        [
            {"pt": "omegawrap", "kf": json.dumps({"key": "omegawrap_carbon", "unit": "per_roll"}), "price": 680},
            {"pt": "omegawrap", "kf": json.dumps({"key": "omegawrap_eglass", "unit": "per_roll"}), "price": 470},
            # A non-wrap row that must NOT be touched.
            {"pt": "compression", "kf": json.dumps({"key": "compression_sleeve", "unit": "flat"}), "price": 300},
        ],
    )
    # A quote + a historical omegawrap line item.
    conn.execute(
        "INSERT INTO quote (id, quote_number, status, tax_amount, revision_number, created_at, updated_at) "
        "VALUES (1, 'QUO-362-001', 'NEW', 0, 0, '2026-08-11 00:00:00', '2026-08-11 00:00:00')"
    )
    conn.executemany(
        "INSERT INTO quote_line_item "
        "(quote_id, product_type, description, quantity, unit_price, line_total, sort_order) "
        "VALUES (:qid, :pt, :desc, :qty, :up, :lt, :so)",
        [
            {"qid": 1, "pt": "omegawrap", "desc": "OmegaWrap Carbon", "qty": 2, "up": 680, "lt": 1360, "so": 1},
            {"qid": 1, "pt": "sleeve", "desc": "Half Sole", "qty": 1, "up": 100, "lt": 100, "so": 2},
        ],
    )
    conn.commit()


def _pt_count(conn: sqlite3.Connection, table: str, value: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE product_type = ?", (value,)
    ).fetchone()[0]


@pytest.fixture()
def seeded_db(tmp_path: Path):
    db_path = tmp_path / "omegawrap.db"
    _alembic(db_path, "upgrade", PREV_REV)
    conn = sqlite3.connect(db_path)
    try:
        _seed(conn)
    finally:
        conn.close()
    return db_path


def test_upgrade_moves_omegawrap_to_composite(seeded_db: Path) -> None:
    # Baseline pricing migrations also seed omegawrap_* rows, so measure the
    # pre-upgrade counts rather than hardcoding — this mirrors real prod.
    conn = sqlite3.connect(seeded_db)
    try:
        pre_pt_omega = _pt_count(conn, "pricing_table", "omegawrap")
        pre_pt_composite = _pt_count(conn, "pricing_table", "composite")
        pre_li_omega = _pt_count(conn, "quote_line_item", "omegawrap")
        pre_pt_compression = _pt_count(conn, "pricing_table", "compression")
        pre_li_sleeve = _pt_count(conn, "quote_line_item", "sleeve")
    finally:
        conn.close()
    assert pre_pt_omega >= 2 and pre_li_omega >= 1  # our seed plus any baseline

    _alembic(seeded_db, "upgrade", THIS_REV)
    conn = sqlite3.connect(seeded_db)
    try:
        # No product_type='omegawrap' survives in either table.
        assert _pt_count(conn, "pricing_table", "omegawrap") == 0
        assert _pt_count(conn, "quote_line_item", "omegawrap") == 0
        # Every omegawrap row became composite.
        assert _pt_count(conn, "pricing_table", "composite") == pre_pt_composite + pre_pt_omega
        assert _pt_count(conn, "quote_line_item", "composite") == pre_li_omega
        # Untouched product types stay put.
        assert _pt_count(conn, "pricing_table", "compression") == pre_pt_compression
        assert _pt_count(conn, "quote_line_item", "sleeve") == pre_li_sleeve
        # Internal variant keys are NOT renamed — still keyed on omegawrap_*.
        keys = {
            json.loads(row[0]).get("key")
            for row in conn.execute(
                "SELECT key_fields FROM pricing_table WHERE product_type = 'composite'"
            ).fetchall()
        }
        assert {"omegawrap_carbon", "omegawrap_eglass"} <= keys
    finally:
        conn.close()


def test_downgrade_restores_omegawrap(seeded_db: Path) -> None:
    conn = sqlite3.connect(seeded_db)
    try:
        pre_pt_omega = _pt_count(conn, "pricing_table", "omegawrap")
        pre_li_omega = _pt_count(conn, "quote_line_item", "omegawrap")
    finally:
        conn.close()

    _alembic(seeded_db, "upgrade", THIS_REV)
    _alembic(seeded_db, "downgrade", PREV_REV)
    conn = sqlite3.connect(seeded_db)
    try:
        assert _pt_count(conn, "pricing_table", "omegawrap") == pre_pt_omega
        assert _pt_count(conn, "quote_line_item", "omegawrap") == pre_li_omega
        assert _pt_count(conn, "pricing_table", "composite") == 0
        assert _pt_count(conn, "quote_line_item", "composite") == 0
    finally:
        conn.close()
