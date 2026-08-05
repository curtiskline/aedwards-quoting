from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import User

ALEMBIC_CMD = [sys.executable, "-m", "alembic"]


def _alembic_env(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return env


def test_dashboard_redirects_to_quotes() -> None:
    app = create_app()
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_migrations_create_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "migrations.db"
    env = _alembic_env(db_path)

    subprocess.run(
        [*ALEMBIC_CMD, "upgrade", "head"],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        conn.close()

    assert "user" in tables
    assert "quote" in tables
    assert "quote_line_item" in tables
    assert "pricing_table" in tables
    assert "shipping_config" in tables
    assert "product_type" in tables
    assert "audit_log" in tables


def test_migrations_seed_pricing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "seeded.db"
    env = _alembic_env(db_path)

    subprocess.run(
        [*ALEMBIC_CMD, "upgrade", "head"],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )

    conn = sqlite3.connect(db_path)
    try:
        row_count = conn.execute("SELECT COUNT(*) FROM pricing_table").fetchone()[0]
    finally:
        conn.close()

    assert row_count > 0


def test_ship_to_confirmation_migration_marks_existing_addresses_unconfirmed(tmp_path: Path) -> None:
    """The production-style upgrade must treat pre-marker rows as unconfirmed."""
    db_path = tmp_path / "ship-to-provenance.db"
    env = _alembic_env(db_path)

    subprocess.run(
        [*ALEMBIC_CMD, "upgrade", "20260730_0001"],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO customer (id, company_name, discount_pct, created_at) VALUES (?, ?, ?, ?)",
            (1, "Existing Customer", 0, "2026-08-05 00:00:00"),
        )
        conn.execute(
            """
            INSERT INTO ship_to_address
              (customer_id, address_line1, city, state, postal_code, country, is_default)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "3200 2nd Street SW", "Calgary", "AB", "T2P 1M4", "US", True),
        )
        conn.commit()
    finally:
        conn.close()

    subprocess.run(
        [*ALEMBIC_CMD, "upgrade", "head"],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ship_to_address)")}
        human_confirmed = conn.execute(
            "SELECT human_confirmed FROM ship_to_address WHERE customer_id = 1"
        ).fetchone()[0]
    finally:
        conn.close()

    assert "human_confirmed" in columns
    assert human_confirmed == 0


def test_pricing_admin_page_and_inline_update(tmp_path: Path) -> None:
    db_path = tmp_path / "admin.db"
    previous_database_url = os.environ.get("DATABASE_URL")
    previous_config_database_url = Config.SQLALCHEMY_DATABASE_URI
    try:
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        subprocess.run(
            [*ALEMBIC_CMD, "upgrade", "head"],
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
        assert b"Admin" in page.data
        assert b"Auto-Ship Pricing Defaults" in page.data
        assert b"Product Types" in page.data
        assert b"Product Catalog" in page.data

        conn = sqlite3.connect(db_path)
        try:
            row_id = conn.execute("SELECT id FROM pricing_table ORDER BY id LIMIT 1").fetchone()[0]
        finally:
            conn.close()

        updated = client.post(f"/admin/pricing/{row_id}", data={"price": "999.99"})
        assert updated.status_code == 200
        assert b"999.99" in updated.data

        shipping_updated = client.post(
            "/admin/shipping-config",
            data={
                "default_rate_per_lb_mile": "0.001200",
                "default_length_ft": "12.50",
                "origin_zip_codes": "74103, 77002",
                "rate_overrides": "sleeve=0.001300\noversleeve=0.001150",
            },
        )
        assert shipping_updated.status_code == 200
        assert b"0.001200" in shipping_updated.data
        assert b"74103, 77002" in shipping_updated.data

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT default_rate_per_lb_mile, default_length_ft, origin_zip_codes_json FROM shipping_config WHERE id = 1"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert abs(float(row[0]) - 0.0012) < 0.000001
        assert abs(float(row[1]) - 12.5) < 0.01
        assert "74103" in str(row[2])

        added_type = client.post(
            "/admin/product-types/add",
            data={"name": "field_service", "display_label": "Field Service"},
        )
        assert added_type.status_code == 200
        assert b"Field Service" in added_type.data

        conn = sqlite3.connect(db_path)
        try:
            type_row = conn.execute(
                "SELECT id FROM product_type WHERE name = 'field_service'"
            ).fetchone()
        finally:
            conn.close()
        assert type_row is not None
        field_service_id = type_row[0]

        updated_type = client.post(
            f"/admin/product-types/{field_service_id}/update",
            data={"display_label": "Field Service Team"},
        )
        assert updated_type.status_code == 200
        assert b"Field Service Team" in updated_type.data

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT display_label, is_active FROM product_type WHERE id = ?",
                (field_service_id,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == "Field Service Team"
        assert int(row[1]) == 0

        pricing_page = client.get("/admin/pricing?tab=pricing")
        assert pricing_page.status_code == 200
        assert b"Field Service Team" in pricing_page.data
        assert b"No pricing rates yet" in pricing_page.data

        added_rate = client.post(
            "/admin/pricing/add",
            data={
                "product_type": "field_service",
                "key": "site_visit",
                "unit": "per day",
                "price": "1250",
            },
        )
        assert added_rate.status_code == 200
        assert b"Site Visit" in added_rate.data
        assert b"1250.00" in added_rate.data

        conn = sqlite3.connect(db_path)
        try:
            pricing_row = conn.execute(
                "SELECT id, key_fields, price FROM pricing_table WHERE product_type = 'field_service'"
            ).fetchone()
        finally:
            conn.close()
        assert pricing_row is not None
        pricing_row_id = pricing_row[0]
        assert "site_visit" in pricing_row[1]
        assert abs(float(pricing_row[2]) - 1250) < 0.01

        edited_rate = client.post(
            f"/admin/pricing/{pricing_row_id}",
            data={
                "edit_key_fields": "true",
                "key": "field_visit",
                "unit": "per day",
                "price": "1300",
            },
        )
        assert edited_rate.status_code == 200
        assert b"Field Visit" in edited_rate.data
        assert b"1300.00" in edited_rate.data
        assert client.post(f"/admin/pricing/{pricing_row_id}/delete").status_code == 200

        added_catalog_item = client.post(
            "/admin/catalog/add",
            data={"sku": "FS-001", "description": "Field service visit", "product_family": "other"},
        )
        assert added_catalog_item.status_code == 200
        assert b"FS-001" in added_catalog_item.data

        conn = sqlite3.connect(db_path)
        try:
            catalog_row = conn.execute(
                "SELECT id FROM product_catalog WHERE sku = 'FS-001'"
            ).fetchone()
        finally:
            conn.close()
        assert catalog_row is not None
        catalog_item_id = catalog_row[0]

        updated_catalog_item = client.post(
            f"/admin/catalog/{catalog_item_id}/update",
            data={
                "sku": "FS-002",
                "description": "Updated field service visit",
                "product_family": "other",
                "is_active": "on",
            },
        )
        assert updated_catalog_item.status_code == 200
        assert b"FS-002" in updated_catalog_item.data

        search_before_deactivation = client.get("/api/product-catalog/search?q=FS-002")
        assert search_before_deactivation.get_json()[0]["sku"] == "FS-002"
        deactivated_catalog_item = client.post(
            f"/admin/catalog/{catalog_item_id}/update",
            data={
                "sku": "FS-002",
                "description": "Updated field service visit",
                "product_family": "other",
                "catalog_filter": "active",
            },
        )
        assert deactivated_catalog_item.status_code == 200
        assert b"FS-002 moved to Inactive" in deactivated_catalog_item.data
        assert b"FS-002" in deactivated_catalog_item.data
        assert client.get("/api/product-catalog/search?q=FS-002").get_json() == []

        removed_catalog_item = client.post(f"/admin/catalog/{catalog_item_id}/delete")
        assert removed_catalog_item.status_code == 200
        assert b"Remove FS-002 from the active product catalog" not in removed_catalog_item.data

        assert (
            client.post(
                "/admin/catalog/add",
                data={"sku": "ZZ-001", "description": "Zulu active", "product_family": "other"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/admin/catalog/add",
                data={"sku": "AA-001", "description": "Alpha active", "product_family": "other"},
            ).status_code
            == 200
        )

        active_catalog = client.get("/admin/pricing?tab=catalog")
        assert active_catalog.status_code == 200
        assert b"catalog_filter=active" in active_catalog.data
        assert b"FS-002" not in active_catalog.data
        assert b"AA-001" in active_catalog.data
        assert b"ZZ-001" in active_catalog.data

        all_catalog = client.get("/admin/pricing?tab=catalog&catalog_filter=all")
        assert all_catalog.status_code == 200
        assert all_catalog.data.index(b"AA-001") < all_catalog.data.index(b"ZZ-001")
        assert all_catalog.data.index(b"ZZ-001") < all_catalog.data.index(b"FS-002")

        inactive_catalog = client.get("/admin/pricing?tab=catalog&catalog_filter=inactive")
        assert inactive_catalog.status_code == 200
        assert b"FS-002" in inactive_catalog.data
        assert b"AA-001" not in inactive_catalog.data
        assert b'id="catalog-search"' in inactive_catalog.data
    finally:
        Config.SQLALCHEMY_DATABASE_URI = previous_config_database_url
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
