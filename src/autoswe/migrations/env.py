"""Alembic environment (async engine; works for SQLite and PostgreSQL)."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from autoswe.db import create_engine
from autoswe.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        from autoswe.config import get_settings  # noqa: PLC0415

        url = get_settings().database_url
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# Arbitrary constant: serialises concurrent `upgrade` calls when several replicas start at once.
_MIGRATION_LOCK_ID = 7_274_812


async def run_migrations_online() -> None:
    engine = create_engine(_url())
    async with engine.connect() as connection:
        if connection.dialect.name == "postgresql":
            await connection.execute(
                text("SELECT pg_advisory_lock(:id)"), {"id": _MIGRATION_LOCK_ID}
            )
            await connection.commit()
        try:
            await connection.run_sync(_do_run)
        finally:
            if connection.dialect.name == "postgresql":
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:id)"), {"id": _MIGRATION_LOCK_ID}
                )
                await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
