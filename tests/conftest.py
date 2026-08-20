"""Pytest configuration for local source imports and database selection.

Ensure tests always exercise the current worktree code instead of any globally
or editable-installed copy from a different checkout.

Database target: tests run on SQLite by default (fast, zero setup). Set
TEST_DATABASE_URL to a PostgreSQL *maintenance* URL (any database the role can
connect to, with CREATEDB rights) to run the same suite against PostgreSQL —
each test then gets its own freshly created, dropped-afterwards database:

    TEST_DATABASE_URL="postgresql://devin@/postgres?host=/var/run/postgresql" \
        python -m pytest

Tests that exercise SQLite-specific behavior on purpose (alembic-on-SQLite
migration tests using the sqlite3 module) ignore this switch and stay on
SQLite.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def db_url(tmp_path):
    """A per-test database URL: SQLite file by default, disposable PostgreSQL
    database when TEST_DATABASE_URL is set."""
    admin_url = os.environ.get("TEST_DATABASE_URL")
    if not admin_url:
        yield f"sqlite:///{tmp_path / 'test.db'}"
        return

    import sqlalchemy as sa

    dbname = f"ae_test_{uuid.uuid4().hex[:12]}"
    admin_engine = sa.create_engine(
        admin_url, isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool
    )
    with admin_engine.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    try:
        yield sa.engine.make_url(admin_url).set(database=dbname).render_as_string(
            hide_password=False
        )
    finally:
        with admin_engine.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE "{dbname}" WITH (FORCE)'))
        admin_engine.dispose()
