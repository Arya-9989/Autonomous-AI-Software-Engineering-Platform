"""job idempotency keys: a client retrying a submission gets the same job back

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23 23:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("request_hash", sa.String(length=64), nullable=True))
        batch_op.create_index(
            "uq_jobs_owner_idempotency_key", ["owner", "idempotency_key"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("uq_jobs_owner_idempotency_key")
        batch_op.drop_column("request_hash")
        batch_op.drop_column("idempotency_key")
