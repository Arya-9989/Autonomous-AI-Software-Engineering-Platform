"""Alembic files for the generated Python project: config, environment and the first revision.

The initial revision creates every entity's table in dependency order (a table after the tables
it references), with its foreign keys (``ON DELETE CASCADE``) and their indexes, and drops them in
reverse. The project's tests build their database by running these migrations and check that
they upgrade, match the models and downgrade cleanly, so the sandbox validates the migrations on a
real database every time it runs the tests.
"""

from __future__ import annotations

from textwrap import dedent

from autoswe.pipeline.artifacts import Entity, EntityField
from autoswe.pipeline.naming import pluralize, snake
from autoswe.pipeline.relations import creation_order, foreign_key

__all__ = [
    "ALEMBIC_INI",
    "ENV_PY",
    "MIGRATIONS_TEST",
    "SCRIPT_MAKO",
    "creation_order",
    "foreign_key",
    "render_initial_migration",
    "table_name",
]

_SA_COLUMN = {
    "string": "sa.String(length=255)",
    "text": "sa.Text()",
    "integer": "sa.Integer()",
    "float": "sa.Float()",
    "boolean": "sa.Boolean()",
    "date": "sa.Date()",
    "datetime": "sa.DateTime(timezone=True)",
}


def _src(text: str) -> str:
    return dedent(text).lstrip("\n")


def table_name(entity_name: str) -> str:
    return snake(pluralize(entity_name))


ALEMBIC_INI = _src("""
    [alembic]
    script_location = migrations
    # The database URL comes from DATABASE_URL (see migrations/env.py).

    [loggers]
    keys = root,sqlalchemy,alembic

    [handlers]
    keys = console

    [formatters]
    keys = generic

    [logger_root]
    level = WARNING
    handlers = console

    [logger_sqlalchemy]
    level = WARNING
    handlers =
    qualname = sqlalchemy.engine

    [logger_alembic]
    level = INFO
    handlers =
    qualname = alembic

    [handler_console]
    class = StreamHandler
    args = (sys.stderr,)
    level = NOTSET
    formatter = generic

    [formatter_generic]
    format = %(levelname)-5.5s [%(name)s] %(message)s
""")

ENV_PY = _src('''
    """Alembic environment: the models' metadata and DATABASE_URL."""

    from logging.config import fileConfig

    from alembic import context
    from sqlalchemy import create_engine, text

    import app.models  # noqa: F401  (registers every table on Base.metadata)
    from app.db import DATABASE_URL, Base

    config = context.config
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)

    # Serialises concurrent upgrades (several replicas starting at once) on PostgreSQL.
    _LOCK_ID = 4_204_207


    def run_migrations_offline() -> None:
        context.configure(url=DATABASE_URL, target_metadata=Base.metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()


    def run_migrations_online() -> None:
        engine = create_engine(DATABASE_URL)
        with engine.connect() as connection:
            postgres = connection.dialect.name == "postgresql"
            if postgres:
                connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _LOCK_ID})
                # The lock is held by the session, so it outlives this transaction; ending it
                # here lets Alembic run (and commit) the migrations in a transaction of its own.
                connection.commit()
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                render_as_batch=connection.dialect.name == "sqlite",
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            if postgres:
                connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _LOCK_ID})
                connection.commit()
        engine.dispose()


    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
''')

SCRIPT_MAKO = _src('''
    """${message}

    Revision ID: ${up_revision}
    Revises: ${down_revision | comma,n}
    Create Date: ${create_date}
    """

    from collections.abc import Sequence

    import sqlalchemy as sa
    from alembic import op
    ${imports if imports else ""}
    revision: str = ${repr(up_revision)}
    down_revision: str | None = ${repr(down_revision)}
    branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
    depends_on: str | Sequence[str] | None = ${repr(depends_on)}


    def upgrade() -> None:
        ${upgrades if upgrades else "pass"}


    def downgrade() -> None:
        ${downgrades if downgrades else "pass"}
''')


def _column(field: EntityField) -> str:
    nullable = "False" if field.required else "True"
    return f'        sa.Column("{field.name}", {_SA_COLUMN[field.type]}, nullable={nullable}),'


def render_initial_migration(entities: list[Entity]) -> str:
    known = {entity.name for entity in entities}
    upgrade: list[str] = []
    downgrade: list[str] = []
    for entity in creation_order(entities):
        table = table_name(entity.name)
        upgrade += [f'    op.create_table(\n        "{table}",',
                    '        sa.Column("id", sa.Integer(), nullable=False),']  # fmt: skip
        upgrade += [_column(field) for field in entity.fields]
        parents = [parent for parent in entity.belongs_to if parent in known]
        for parent in parents:
            upgrade.append(
                f'        sa.Column("{foreign_key(parent)}", sa.Integer(), nullable=False),'
            )
        upgrade.append(
            '        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),'
        )
        for parent in parents:
            upgrade.append(
                f'        sa.ForeignKeyConstraint(["{foreign_key(parent)}"], '
                f'["{table_name(parent)}.id"], ondelete="CASCADE"),'
            )
        upgrade += ['        sa.PrimaryKeyConstraint("id"),', "    )"]
        for parent in parents:
            index = f"ix_{table}_{foreign_key(parent)}"
            upgrade.append(f'    op.create_index("{index}", "{table}", ["{foreign_key(parent)}"])')
            downgrade.insert(0, f'    op.drop_index("{index}", table_name="{table}")')
        downgrade.append(f'    op.drop_table("{table}")')
    # Drop children before the tables they reference: reverse of the creation order.
    tables = [line for line in downgrade if "drop_table" in line]
    indexes = [line for line in downgrade if "drop_index" in line]
    downgrade = indexes + list(reversed(tables))
    return (
        _src('''
        """initial schema

        Revision ID: 0001
        Revises:
        Create Date: 2025-01-01 00:00:00
        """

        from collections.abc import Sequence

        import sqlalchemy as sa
        from alembic import op

        revision: str = "0001"
        down_revision: str | None = None
        branch_labels: str | Sequence[str] | None = None
        depends_on: str | Sequence[str] | None = None


        def upgrade() -> None:
        ''')
        + "\n".join(upgrade)
        + "\n\n\ndef downgrade() -> None:\n"
        + "\n".join(downgrade)
        + "\n"
    )


MIGRATIONS_TEST = _src('''
    """The migrations upgrade a fresh database, match the models exactly, and downgrade cleanly.

    Runs on SQLite always, and on PostgreSQL too when TEST_DATABASE_URL points at a database that
    may be emptied (for example postgresql+psycopg://app:app@localhost:5432/app_test).
    """

    import os
    from pathlib import Path

    import pytest
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import create_engine, inspect

    from app.db import Base

    ROOT = Path(__file__).resolve().parents[1]
    POSTGRES_URL = os.getenv("TEST_DATABASE_URL", "")


    @pytest.fixture(params=["sqlite", "postgresql"])
    def database(
        request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[Config, str]:
        if request.param == "sqlite":
            url = f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"
        elif POSTGRES_URL:
            url = POSTGRES_URL
        else:
            pytest.skip("set TEST_DATABASE_URL to check the migrations on PostgreSQL")
        monkeypatch.setattr("app.db.DATABASE_URL", url)  # migrations/env.py reads it per run
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.downgrade(config, "base")  # start from nothing, even on a reused database
        return config, url


    def test_upgrade_matches_the_models_and_downgrade_removes_everything(
        database: tuple[Config, str],
    ) -> None:
        config, url = database
        command.upgrade(config, "head")
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                drift = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert drift == [], drift
            command.downgrade(config, "base")
            assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
            command.upgrade(config, "head")
            assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
            command.downgrade(config, "base")
        finally:
            engine.dispose()
''')
