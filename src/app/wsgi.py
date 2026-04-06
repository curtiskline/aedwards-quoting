"""WSGI entrypoint for gunicorn."""

from __future__ import annotations

from . import create_app

app = create_app()


def main() -> None:
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
