"""job retries: transient-failure retry count and the earliest time a requeued job may run

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-23 23:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import autoswe.models  # custom column types referenced below

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("retries", sa.Integer(), server_default="0", nullable=False))
        batch_op.add_column(
            sa.Column("run_after", autoswe.models.UTCDateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("run_after")
        batch_op.drop_column("retries")
