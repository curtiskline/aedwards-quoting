from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import User


def _alembic_env(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return env


def test_dashboard_page_loads() -> None:
    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_migrations_create_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "migrations.db"
    env = _alembic_env(db_path)

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


def test_migrations_seed_pricing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "seeded.db"
    env = _alembic_env(db_path)

    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=Path(__file__).resolve().parents[1], env=env)

    conn = sqlite3.connect(db_path)
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM pricing_table").fetchone()[0]
    finally:
        conn.close()

    assert row_count > 0


def test_pricing_admin_page_and_inline_update(tmp_path: Path) -> None:
    db_path = tmp_path / "admin.db"
    previous_database_url = os.environ.get("DATABASE_URL")
    previous_config_database_url = Config.SQLALCHEMY_DATABASE_URI
    try:
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True,
            cwd=Path(__file__).resolve().parents[1],
            env=_alembic_env(db_path),
        )

        Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
        app = create_app()
        client = app.test_client()

        with app.app_context():
            user = User(email="owner@example.com", name="Owner", password_hash="")
            user.set_password("secret123")
            db.session.add(user)
            db.session.commit()

        client.post(
            "/auth/password",
            data={"email": "owner@example.com", "password": "secret123"},
            follow_redirects=True,
        )

        page = client.get("/admin/pricing")
        assert page.status_code == 200
        assert b"Pricing Administration" in page.data

        conn = sqlite3.connect(db_path)
        try:
            row_id = conn.execute("SELECT id FROM pricing_table ORDER BY id LIMIT 1").fetchone()[0]
        finally:
            conn.close()

        updated = client.post(f"/admin/pricing/{row_id}", data={"price": "999.99"})
        assert updated.status_code == 200
        assert b"999.99" in updated.data
    finally:
        Config.SQLALCHEMY_DATABASE_URI = previous_config_database_url
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
