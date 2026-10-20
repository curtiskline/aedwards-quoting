#!/usr/bin/env python3
"""Idempotently seed the verified 2026 price-sheet product ranges on staging.

This is intentionally staging-only.  The source sheets do not define part
numbers for steel or girth-weld sleeve families, so those rows retain NULL
part numbers.  Product identifiers must be approved by Chip before production.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

STAGING_HOSTNAME = "aedwards-staging"
DEFAULT_ENV_FILE = Path("/opt/aedwards/.env")
_FALSY = {"0", "false", "no", "off"}

CATALOG_RANGES = (
    # October 2026 price list. No source part numbers exist.
    (None, '1/4" Thick Steel Sleeves', "sleeve", 2, 48, "0.25", "0.25"),
    (None, '5/16" Thick Steel Sleeves', "sleeve", 2, 48, "0.3125", "0.3125"),
    (None, '3/8" Thick Steel Sleeves', "sleeve", 2, 48, "0.375", "0.375"),
    (None, '1/2" Thick Steel Sleeves', "sleeve", 2, 48, "0.5", "0.5"),
    (None, '2-16" Girth Weld Sleeves', "girth_weld", 2, 16, "0.25", "0.375"),
    (None, '18"-30" Girth Weld Sleeves', "girth_weld", 18, 30, "0.25", "0.5"),
    (None, '32"-44" Girth Weld Sleeves', "girth_weld", 32, 44, "0.25", "0.5"),
    # April 2026 bag sheet. These part numbers are source-authored.
    ("GTW 10-12", "GTW 10-12", "bag", 10, 12, None, None),
    ("GTW 16", "GTW 16", "bag", 14, 18, None, None),
    ("GTW 20-24", "GTW 20-24", "bag", 20, 26, None, None),
    ("GTW 30-36", "GTW 30-36", "bag", 30, 38, None, None),
    ("GTW 42-48", "GTW 42-48", "bag", 40, 48, None, None),
    ("Soft Set", "Soft Set", "bag", None, None, None, None),
)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip("'\"")


def _assert_staging(args: argparse.Namespace) -> None:
    if not args.i_am_staging:
        sys.exit("REFUSING: pass --i-am-staging on the staging host")
    if socket.gethostname() != STAGING_HOSTNAME:
        sys.exit(
            f"REFUSING: hostname is {socket.gethostname()!r}, expected {STAGING_HOSTNAME!r}"
        )
    delivery = os.environ.get("EMAIL_DELIVERY_ENABLED", "").strip().lower()
    if delivery not in _FALSY:
        sys.exit(
            "REFUSING: staging catalog seed requires EMAIL_DELIVERY_ENABLED=false"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--i-am-staging", action="store_true")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    args = parser.parse_args()
    _load_env_file(args.env_file)
    _assert_staging(args)

    from app import create_app
    from app.extensions import db
    from app.models import ProductCatalog

    app = create_app()
    created = 0
    updated = 0
    with app.app_context():
        for part_number, description, product_type, dia_min, dia_max, wall_min, wall_max in CATALOG_RANGES:
            query = db.session.query(ProductCatalog)
            row = (
                query.filter(ProductCatalog.part_number == part_number).first()
                if part_number is not None
                else query.filter(
                    ProductCatalog.part_number.is_(None),
                    ProductCatalog.description == description,
                ).first()
            )
            if row is None:
                row = ProductCatalog(part_number=part_number, description=description)
                db.session.add(row)
                created += 1
            else:
                updated += 1
            row.description = description
            row.product_type = product_type
            row.diameter_min = Decimal(str(dia_min)) if dia_min is not None else None
            row.diameter_max = Decimal(str(dia_max)) if dia_max is not None else None
            row.wall_thickness_min = Decimal(wall_min) if wall_min is not None else None
            row.wall_thickness_max = Decimal(wall_max) if wall_max is not None else None
            row.is_active = True
        db.session.commit()
    print(f"Seeded staging catalog ranges: {created} created, {updated} updated")


if __name__ == "__main__":
    main()
