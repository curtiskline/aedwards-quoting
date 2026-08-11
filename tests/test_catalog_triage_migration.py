"""Round-trip test for migration 20260810_0003 against a copy of prod data.

The 66 type-unset rows (old OTHER 63 + PIPE_JACK 3) are triaged into real
Types by part_number pattern, and product_family is dropped for good. We drive
the real alembic migrations (0002 then 0003) over a faithful copy of the 225
production ProductCatalog rows and assert the exact resulting Type counts.

Local dev DB is stale and has no catalog table, so the fixture
(tests/fixtures_product_catalog_prod.json) is a snapshot of the live prod
catalog (part numbers + descriptions + old family — no customer data), pulled
2026-08-11 while prod was still at the pre-0002 schema.
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
FIXTURE = Path(__file__).resolve().parent / "fixtures_product_catalog_prod.json"

# Expected product_type counts after 0003 upgrade, verified against prod.
EXPECTED_COUNTS = {
    "sleeve": 92,
    "girth_weld": 61,
    "composite": 24,
    "accessory": 22,
    "bag": 11,
    "compression": 13,
}
EXPECTED_UNTYPED = 2  # '207-HB-EPOXY' and 'Product List'
EXPECTED_TOTAL = 225


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


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _type_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT product_type, COUNT(*) FROM product_catalog GROUP BY product_type"
        ).fetchall()
    }


def _load_prod_catalog(conn: sqlite3.Connection) -> None:
    """Insert the pre-0002 prod rows (sku + product_family) into product_catalog."""
    rows = json.loads(FIXTURE.read_text())
    assert len(rows) == EXPECTED_TOTAL, f"fixture drifted: {len(rows)} rows"
    conn.executemany(
        "INSERT INTO product_catalog (id, sku, description, product_family, is_active) "
        "VALUES (:id, :sku, :description, :product_family, :is_active)",
        rows,
    )
    conn.commit()


@pytest.fixture()
def prod_copy_db(tmp_path: Path):
    db_path = tmp_path / "prod_copy.db"
    # Build the schema up to the pre-0002 revision, then load real prod rows.
    _alembic(db_path, "upgrade", "20260810_0001")
    conn = sqlite3.connect(db_path)
    try:
        _load_prod_catalog(conn)
    finally:
        conn.close()
    return db_path


def test_upgrade_assigns_types_and_drops_family(prod_copy_db: Path) -> None:
    _alembic(prod_copy_db, "upgrade", "20260810_0003")

    conn = sqlite3.connect(prod_copy_db)
    try:
        cols = _columns(conn, "product_catalog")
        assert "product_family" not in cols, "product_family must be dropped"
        assert "part_number" in cols and "product_type" in cols

        counts = _type_counts(conn)
        for slug, expected in EXPECTED_COUNTS.items():
            assert counts.get(slug) == expected, f"{slug}: {counts.get(slug)} != {expected}"
        assert counts.get(None) == EXPECTED_UNTYPED

        total = conn.execute("SELECT COUNT(*) FROM product_catalog").fetchone()[0]
        assert total == EXPECTED_TOTAL

        # The two deliberately-untyped non-product rows stay NULL.
        untyped = {
            row[0]
            for row in conn.execute(
                "SELECT part_number FROM product_catalog WHERE product_type IS NULL"
            ).fetchall()
        }
        assert untyped == {"207-HB-EPOXY", "Product List"}

        # 'SLV BACKINGSTRIP' is an accessory, NOT swept up by the 'SLV %' rule.
        row = conn.execute(
            "SELECT product_type FROM product_catalog WHERE part_number = 'SLV BACKINGSTRIP'"
        ).fetchone()
        assert row[0] == "accessory"
    finally:
        conn.close()


def test_down_up_round_trip(prod_copy_db: Path) -> None:
    _alembic(prod_copy_db, "upgrade", "20260810_0003")
    # Downgrade back to 0002: product_family is re-added and repopulated.
    _alembic(prod_copy_db, "downgrade", "20260810_0002")

    conn = sqlite3.connect(prod_copy_db)
    try:
        cols = _columns(conn, "product_catalog")
        assert "product_family" in cols, "downgrade must restore product_family"

        nulls = conn.execute(
            "SELECT COUNT(*) FROM product_catalog WHERE product_family IS NULL"
        ).fetchone()[0]
        assert nulls == 0, "downgrade must repopulate every product_family"

        # Best-effort reverse map: the six clean types round-trip, the rest -> OTHER.
        fam = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT part_number, product_family FROM product_catalog "
                "WHERE part_number IN ('SLV 4.500 .250', 'CK-620', 'OW-MAGNET', 'GTWeight-16-EM-INC')"
            ).fetchall()
        }
        assert fam["SLV 4.500 .250"] == "SLEEVE"
        assert fam["OW-MAGNET"] == "OMEGAWRAP"
        assert fam["GTWeight-16-EM-INC"] == "BAG"
        assert fam["CK-620"] == "OTHER"  # accessory has no clean family -> OTHER
    finally:
        conn.close()

    # Upgrade again: counts must be identical (idempotent, no double-assignment).
    _alembic(prod_copy_db, "upgrade", "20260810_0003")
    conn = sqlite3.connect(prod_copy_db)
    try:
        assert "product_family" not in _columns(conn, "product_catalog")
        counts = _type_counts(conn)
        for slug, expected in EXPECTED_COUNTS.items():
            assert counts.get(slug) == expected, f"re-upgrade {slug}: {counts.get(slug)} != {expected}"
        assert counts.get(None) == EXPECTED_UNTYPED
    finally:
        conn.close()
