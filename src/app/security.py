"""Session-token CSRF protection (T514 slice 2, audit T515 finding M3).

No new dependency: a per-session random token, required on every
state-changing request. Browser paths supply it two ways:

- plain form posts carry a hidden ``csrf_token`` input ({{ csrf_field() }});
- htmx requests get an ``X-CSRF-Token`` header from the one listener in
  layout.html, which covers hx-post buttons that live outside any form.

Every POST endpoint in this app is a browser form or htmx action (email
intake runs through the worker, not HTTP), so there is no exemption list.

Pytest note: the suite's hundreds of existing posts predate tokens, so
enforcement is skipped while PYTEST_CURRENT_TEST is set unless a test opts
in with CSRF_ENFORCE_UNDER_PYTEST — the dedicated CSRF tests do exactly
that, so the guard itself stays tested.
"""

from __future__ import annotations

import os
import secrets

from flask import Flask, abort, current_app, request, session
from markupsafe import Markup

CSRF_SESSION_KEY = "_csrf_token"


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_field() -> Markup:
    return Markup(
        f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'
    )


def _should_enforce() -> bool:
    if not current_app.config.get("CSRF_ENABLED", True):
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") and not current_app.config.get(
        "CSRF_ENFORCE_UNDER_PYTEST"
    ):
        return False
    return True


def install_csrf(app: Flask) -> None:
    @app.before_request
    def _require_csrf_token():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        if not _should_enforce():
            return None
        expected = session.get(CSRF_SESSION_KEY)
        supplied = request.form.get("csrf_token") or request.headers.get(
            "X-CSRF-Token"
        )
        if (
            not expected
            or not supplied
            or not secrets.compare_digest(supplied, expected)
        ):
            abort(
                400,
                description=(
                    "This page's security token is missing or stale. "
                    "Reload the page and try again."
                ),
            )
        return None

    @app.context_processor
    def _inject_csrf() -> dict:
        return {"csrf_field": csrf_field, "csrf_token": csrf_token}
