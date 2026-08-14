"""Canonical fiscal-year quote-number generator.

This is the single source of truth for quote numbers. Every path that mints a
number — the web app (manual create + routes), the e-responder DB writer, and
the monitor's DB write path — calls :func:`generate_quote_number` so the
sequence logic exists in exactly one place.

History: three separate copies of this logic used to exist (app.quotes,
db_writer, monitor). The collision bug behind the 2026-08-13 prod outage lived
in one copy (db_writer's string-max + split("-")[-1]) while another was fine —
exactly the failure mode duplication invites. Consolidated under task 377.

Requires a Flask application context (uses ``app.extensions.db``).
"""

from __future__ import annotations

import re
from datetime import datetime

from .extensions import db
from .models import Quote


def _fiscal_prefix(year: int) -> str:
    """Fiscal-year prefix: last digit of century + two-digit year (2026 -> 126)."""
    return f"1{year % 100}"


def generate_quote_number() -> str:
    """Generate the next sequential quote number for the current fiscal year.

    Shape is ``{prefix}-{seq:03d}`` (e.g. ``126-001`` for 2026). The next
    sequence is the numeric max of the SEQUENCE segment across existing base
    quote numbers for this prefix, plus one.

    Revisions append a suffix (``{prefix}-{seq}-R{n}`` /  ``{prefix}-{seq}-{nn}``,
    see routes._revision_quote_number). The matching regex anchors on the base
    shape ``^{prefix}-(\\d+)$`` so revision-suffixed numbers are ignored entirely
    rather than being misread as the sequence. The old code took the string max
    and read ``split("-")[-1]`` as the sequence; once revisions existed that
    grabbed the revision suffix and regenerated an existing number, failing the
    UNIQUE constraint and blocking ALL new auto-quotes (prod outage 2026-08-13).
    """
    year = datetime.utcnow().year
    prefix = _fiscal_prefix(year)
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")

    sequences = (
        int(match.group(1))
        for (quote_number,) in db.session.query(Quote.quote_number)
        .filter(Quote.quote_number.like(f"{prefix}-%"))
        .all()
        if (match := pattern.match(quote_number))
    )
    seq = max(sequences, default=0) + 1
    return f"{prefix}-{seq:03d}"
