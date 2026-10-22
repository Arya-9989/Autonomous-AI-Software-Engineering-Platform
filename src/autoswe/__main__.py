"""Command-line entry point: ``autoswe serve`` (default) or ``autoswe migrate``."""

from __future__ import annotations

import argparse

import uvicorn

from autoswe.config import get_settings
from autoswe.logging_config import configure_logging
from autoswe.migrations.runner import upgrade_to_head


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="autoswe")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the API, UI and in-process worker (default)")
    serve.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev)")
    sub.add_parser("migrate", help="Apply database migrations and exit")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.command == "migrate":
        configure_logging(settings.log_level, settings.log_format)
        upgrade_to_head(settings.database_url)
        return

    uvicorn.run(
        "autoswe.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=getattr(args, "reload", False),
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        log_config=None,
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
