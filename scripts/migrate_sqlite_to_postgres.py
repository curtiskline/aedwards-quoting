#!/usr/bin/env python3
"""Copy a live SQLite database into PostgreSQL, verbatim, with verification.

Usage:
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite /opt/aedwards/instance/allenedwards.db \
        --postgres postgresql://aedwards@/aedwards \
        [--recreate]

Behavior:
- The TARGET must be empty (every application table at zero rows). A non-empty
  target is refused unless --recreate is given, in which case the target's
  public schema is dropped and rebuilt via `alembic upgrade head` first. This
  makes the script safely re-runnable: a partial or stale load is never
  appended to.
- The SOURCE must be at the same alembic revision as the target schema. Both
  sides are checked; a mismatch aborts before any row is copied.
- All application tables are copied with their primary keys intact, in
  FK-dependency order, using the app's SQLAlchemy metadata so SQLite's loose
  storage (datetime strings, integer booleans, JSON text, attachment BLOBs)
  lands as the proper PostgreSQL types.
- After the copy, every integer-PK sequence is realigned to MAX(id).
- A verification report compares per-table row counts, attachment BLOB
  checksums, quote statuses and spot-samples JSON fields. Any mismatch exits
  non-zero.

The source database is only ever read. Stop writers (aedwards-monitor and
aedwards-web) before the FINAL sync of a cutover so no rows land after the
copy; rehearsal runs against a file copy need no such step.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402

BATCH_SIZE = 500


def _connect_metadata():
    """Import the app's model metadata without needing a Flask app context."""
    import app.models  # noqa: F401
    from app.extensions import db  # noqa: F401  (registers models on import)

    # A SQL NULL read from the source comes back as Python None; with
    # SQLAlchemy's default none_as_null=False that would be re-inserted as the
    # JSON value 'null' instead of SQL NULL, silently changing the data. Flip
    # the flag on every JSON column for the duration of the copy.
    for table in db.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, sa.JSON):
                col.type.none_as_null = True

    return db.metadata


def _alembic_version(conn) -> str | None:
    try:
        return conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    except sa.exc.DatabaseError:
        return None


def _recreate_target(pg_url: str) -> None:
    print("--recreate: dropping and rebuilding target schema ...")
    engine = sa.create_engine(pg_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    engine.dispose()
    env = dict(os.environ, DATABASE_URL=pg_url)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def _blob_digest(conn, dialect: str) -> list[tuple]:
    """Per-attachment sha256 of content_bytes, computed client-side so the
    same code runs on both engines."""
    rows = conn.execute(
        sa.text("SELECT id, content_bytes FROM quote_attachment ORDER BY id")
    ).fetchall()
    return [(r[0], hashlib.sha256(r[1] if r[1] is not None else b"").hexdigest()) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="Path to the source SQLite file")
    parser.add_argument("--postgres", required=True, help="Target PostgreSQL URL")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Drop and rebuild the target schema before loading (required to re-run)",
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.is_file():
        print(f"ERROR: source SQLite file not found: {sqlite_path}", file=sys.stderr)
        return 2

    if args.recreate:
        _recreate_target(args.postgres)

    metadata = _connect_metadata()
    src_engine = sa.create_engine(f"sqlite:///{sqlite_path}")
    dst_engine = sa.create_engine(args.postgres)

    with src_engine.connect() as s, dst_engine.connect() as d:
        src_rev, dst_rev = _alembic_version(s), _alembic_version(d)
        if dst_rev is None:
            print(
                "ERROR: target has no alembic_version table. Run "
                "`alembic upgrade head` against it first (or pass --recreate).",
                file=sys.stderr,
            )
            return 2
        if src_rev != dst_rev:
            print(
                f"ERROR: alembic revision mismatch: source={src_rev} target={dst_rev}. "
                "Upgrade the source deployment before migrating.",
                file=sys.stderr,
            )
            return 2

        tables = metadata.sorted_tables
        # Refuse a non-empty target: never append to a partial/stale load.
        # Tables the migration chain itself seeds are exempt from the check
        # (a freshly upgraded schema legitimately has rows there); they are
        # truncated below so the source remains the single source of truth.
        seeded_by_migrations = {"pricing_table", "product_type", "shipping_config"}
        dirty = [
            t.name
            for t in tables
            if t.name not in seeded_by_migrations
            and d.execute(sa.select(sa.func.count()).select_from(t)).scalar()
        ]
        if dirty:
            print(
                f"ERROR: target is not empty (rows in: {', '.join(dirty)}). "
                "Re-run with --recreate to drop and reload it.",
                file=sys.stderr,
            )
            return 2

        # Clear migration-seeded rows (and reset identities) so the copy is
        # verbatim, not seeds-plus-source.
        all_names = ", ".join(f'"{t.name}"' for t in tables)
        d.execute(sa.text(f"TRUNCATE {all_names} RESTART IDENTITY CASCADE"))

        print(f"Copying {len(tables)} tables (alembic revision {src_rev}) ...")
        counts: dict[str, tuple[int, int]] = {}
        for table in tables:
            # Ascending-PK order keeps self-referential FKs valid mid-copy
            # (quote.replaces_quote_id always points at an older, smaller id).
            query = sa.select(table)
            pk_cols = list(table.primary_key.columns)
            if pk_cols:
                query = query.order_by(*pk_cols)
            rows = s.execute(query).mappings().all()
            for i in range(0, len(rows), BATCH_SIZE):
                batch = [dict(r) for r in rows[i : i + BATCH_SIZE]]
                if batch:
                    d.execute(table.insert(), batch)
            copied = d.execute(sa.select(sa.func.count()).select_from(table)).scalar()
            counts[table.name] = (len(rows), copied)
            print(f"  {table.name:28s} {len(rows):6d} -> {copied:6d}")

        # Realign identity sequences past the copied explicit ids.
        for table in tables:
            pk_cols = list(table.primary_key.columns)
            if len(pk_cols) == 1 and isinstance(pk_cols[0].type, sa.Integer):
                col = pk_cols[0].name
                d.execute(
                    sa.text(
                        f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', '{col}'), "
                        f'COALESCE((SELECT MAX("{col}") FROM "{table.name}"), 1))'
                    )
                )

        d.commit()

        # ---- Verification report -------------------------------------------
        print("\nVerification:")
        failures: list[str] = []
        for name, (src_count, dst_count) in counts.items():
            ok = src_count == dst_count
            if not ok:
                failures.append(f"row count mismatch in {name}: {src_count} != {dst_count}")
            print(f"  rows {name:28s} src={src_count:6d} dst={dst_count:6d} {'OK' if ok else 'MISMATCH'}")

        src_blobs, dst_blobs = _blob_digest(s, "sqlite"), _blob_digest(d, "postgresql")
        blob_ok = src_blobs == dst_blobs
        if not blob_ok:
            failures.append("attachment content_bytes sha256 mismatch")
        print(f"  quote_attachment sha256: {len(src_blobs)} blobs {'OK' if blob_ok else 'MISMATCH'}")

        for probe, label in [
            ("SELECT status, COUNT(*) FROM quote GROUP BY status ORDER BY status", "quote statuses"),
            ("SELECT MIN(quote_number), MAX(quote_number) FROM quote", "quote number range"),
            ("SELECT COUNT(*) FROM quote WHERE ship_to_json IS NOT NULL", "ship_to_json present"),
            ("SELECT COUNT(*) FROM quote_version WHERE line_items_snapshot IS NOT NULL", "version snapshots"),
        ]:
            src_val = [tuple(str(c) for c in r) for r in s.execute(sa.text(probe)).fetchall()]
            dst_val = [tuple(str(c) for c in r) for r in d.execute(sa.text(probe)).fetchall()]
            ok = src_val == dst_val
            if not ok:
                failures.append(f"spot check mismatch: {label}: {src_val} != {dst_val}")
            print(f"  spot {label:28s} {'OK' if ok else 'MISMATCH'} {src_val}")

        if failures:
            print("\nMIGRATION FAILED VERIFICATION:", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            return 1

    print("\nMigration complete and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
