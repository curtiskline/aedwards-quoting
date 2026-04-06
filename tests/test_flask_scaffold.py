from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from app import create_app


def test_dashboard_page_loads() -> None:
    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_migrations_create_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "migrations.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=Path(__file__).resolve().parents[1], env=env)

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
      conn.close()

    assert "user" in tables
    assert "quote" in tables
    assert "quote_line_item" in tables
    assert "pricing_table" in tables
    assert "audit_log" in tables
