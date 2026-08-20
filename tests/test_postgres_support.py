"""PostgreSQL support: alembic-from-scratch and the SQLite -> PG data copy.

These tests need a real PostgreSQL server and are skipped unless
TEST_DATABASE_URL is set (see conftest.py). They are the executable form of
the CP-1 migration acceptance criteria:

- the alembic chain runs clean on an empty PostgreSQL database;
- the app works against the result (enum round-trip, BLOBs, JSON, sequences);
- scripts/migrate_sqlite_to_postgres.py copies a head-revision SQLite database
  verbatim, verifies itself, refuses a non-empty target, and reloads with
  --recreate.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set (PostgreSQL required)",
)


def _alembic_upgrade_head(database_url: str) -> None:
    env = dict(os.environ, DATABASE_URL=database_url)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def sqlite_source(tmp_path):
    """A head-revision SQLite database seeded with representative data,
    including the awkward cases: enum-name statuses, attachment BLOBs,
    JSON documents, a revision chain, and every auxiliary table."""
    db_path = tmp_path / "source.db"
    _alembic_upgrade_head(f"sqlite:///{db_path}")

    from datetime import datetime

    from app import create_app
    from app.config import Config
    from app.extensions import db
    from app.models import (
        AuditLog,
        Contact,
        Customer,
        FailedIntake,
        ProcessedInboundEmail,
        Quote,
        QuoteAttachment,
        QuoteLineItem,
        QuoteStatus,
        QuoteVersion,
        RejectedEmail,
        ShipToAddress,
        User,
    )

    old_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path}"
    app = create_app()
    try:
        with app.app_context():
            user = User(email="chip@example.com", name="Chip", password_hash="x")
            customer = Customer(company_name="Buckeye", discount_pct=5)
            customer.contacts.append(Contact(name="Pat", email="pat@example.com"))
            customer.ship_to_addresses.append(
                ShipToAddress(
                    address_line1="1 Main St",
                    city="Tulsa",
                    state="OK",
                    postal_code="74103",
                    human_confirmed=True,
                )
            )
            db.session.add_all([user, customer])
            db.session.flush()

            blob = bytes(range(256)) * 512  # 128 KiB, every byte value
            original = Quote(
                quote_number="126-001",
                customer_id=customer.id,
                status=QuoteStatus.REPLACED,
                ship_to_json={"city": "Tulsa", "state": "OK"},
                bill_to_json={"company": "Buckeye"},
            )
            original.line_items.append(
                QuoteLineItem(
                    product_type="sleeve",
                    description="Sleeve",
                    quantity=2,
                    unit_price=10,
                    line_total=20,
                    specs_json={"wall_thickness": "0.375", "grade": 50},
                )
            )
            original.attachments.append(
                QuoteAttachment(
                    filename="rfq.pdf",
                    content_type="application/pdf",
                    size_bytes=len(blob),
                    content_bytes=blob,
                )
            )
            original.versions.append(
                QuoteVersion(
                    version_number=1,
                    pdf_path="quote-126-001-v1.pdf",
                    artifact_status="retained",
                    line_items_snapshot=[{"description": "Sleeve", "line_total": "20.00"}],
                    sent_at=datetime(2026, 8, 1, 12, 0, 0),
                    sent_to="pat@example.com",
                )
            )
            original.audit_logs.append(AuditLog(action="sent", details={"to": "pat@example.com"}))
            db.session.add(original)
            db.session.flush()

            revision = Quote(
                quote_number="126-001-R1",
                customer_id=customer.id,
                status=QuoteStatus.IN_REVIEW,
                replaces_quote_id=original.id,
                revision_number=1,
            )
            db.session.add(revision)
            db.session.add(ProcessedInboundEmail(source_email_id="msg-123"))
            db.session.add(
                FailedIntake(
                    received_at=datetime(2026, 8, 13, 9, 0, 0),
                    sender_email="x@example.com",
                    failure_stage="parse_truncated",
                    error_detail="boom",
                )
            )
            db.session.add(
                RejectedEmail(received_at=datetime(2026, 8, 10, 8, 0, 0), subject="spam")
            )
            db.session.commit()
    finally:
        Config.SQLALCHEMY_DATABASE_URI = old_uri
    return db_path


def _run_migration(db_path, pg_url, *extra):
    return subprocess.run(
        [
            sys.executable,
            "scripts/migrate_sqlite_to_postgres.py",
            "--sqlite",
            str(db_path),
            "--postgres",
            pg_url,
            *extra,
        ],
        cwd=ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
    )


def test_alembic_upgrade_head_on_empty_postgres(db_url):
    _alembic_upgrade_head(db_url)

    import sqlalchemy as sa

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        statuses = [
            r[0]
            for r in conn.execute(sa.text("SELECT unnest(enum_range(NULL::quote_status))"))
        ]
        assert statuses == [
            "NEW",
            "IN_REVIEW",
            "NEEDS_PRICING",
            "READY",
            "SENT",
            "ARCHIVED",
            "REPLACED",
        ]
        assert conn.execute(sa.text("SELECT COUNT(*) FROM pricing_table")).scalar() > 0
        # Seeded explicit ids must not leave the sequence behind.
        next_id = conn.execute(
            sa.text("SELECT nextval(pg_get_serial_sequence('product_type', 'id'))")
        ).scalar()
        max_id = conn.execute(sa.text("SELECT MAX(id) FROM product_type")).scalar()
        assert next_id > max_id
    engine.dispose()


def test_sqlite_to_postgres_copy_round_trips(sqlite_source, db_url):
    _alembic_upgrade_head(db_url)
    result = _run_migration(sqlite_source, db_url)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Migration complete and verified." in result.stdout

    # Read everything back through the app, on PostgreSQL.
    from app import create_app
    from app.config import Config
    from app.extensions import db
    from app.models import Quote, QuoteStatus

    old_uri = Config.SQLALCHEMY_DATABASE_URI
    Config.SQLALCHEMY_DATABASE_URI = db_url
    app = create_app()
    try:
        with app.app_context():
            original = Quote.query.filter_by(quote_number="126-001").one()
            assert original.status is QuoteStatus.REPLACED
            assert original.ship_to_json == {"city": "Tulsa", "state": "OK"}
            assert original.attachments[0].content_bytes == bytes(range(256)) * 512
            assert original.versions[0].line_items_snapshot == [
                {"description": "Sleeve", "line_total": "20.00"}
            ]
            assert original.replaced_by.quote_number == "126-001-R1"
            assert original.replaced_by.status is QuoteStatus.IN_REVIEW

            # Sequences must be past the copied ids: a new insert may not
            # collide with migrated rows.
            fresh = Quote(quote_number="126-002", status=QuoteStatus.NEW)
            db.session.add(fresh)
            db.session.commit()
            assert fresh.id > original.replaced_by.id
    finally:
        Config.SQLALCHEMY_DATABASE_URI = old_uri


def test_migration_refuses_non_empty_target_then_reloads_with_recreate(sqlite_source, db_url):
    _alembic_upgrade_head(db_url)
    first = _run_migration(sqlite_source, db_url)
    assert first.returncode == 0, first.stdout + first.stderr

    rerun = _run_migration(sqlite_source, db_url)
    assert rerun.returncode == 2
    assert "target is not empty" in rerun.stderr

    recreate = _run_migration(sqlite_source, db_url, "--recreate")
    assert recreate.returncode == 0, recreate.stdout + recreate.stderr
    assert "Migration complete and verified." in recreate.stdout


def test_migration_refuses_revision_mismatch(sqlite_source, db_url, tmp_path):
    """A source that is not at the target's alembic head must be refused."""
    _alembic_upgrade_head(db_url)
    stale = tmp_path / "stale.db"
    import sqlite3

    with sqlite3.connect(sqlite_source) as src, sqlite3.connect(stale) as dst:
        src.backup(dst)
        dst.execute("UPDATE alembic_version SET version_num = '20260406_0001'")
        dst.commit()

    result = _run_migration(stale, db_url)
    assert result.returncode == 2
    assert "revision mismatch" in result.stderr
