"""token usage, cost and per-principal budgets

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import autoswe.models  # custom column types referenced below

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "principal_usage",
        sa.Column("principal", sa.String(length=128), nullable=False),
        sa.Column("period_start", autoswe.models.UTCDateTime(timezone=True), nullable=False),
        sa.Column("used_tokens", sa.BigInteger(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_read_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cache_creation_input_tokens", sa.BigInteger(), nullable=False),
        sa.Column("cost_microusd", sa.BigInteger(), nullable=False),
        sa.Column("unpriced_calls", sa.Integer(), nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("updated_at", autoswe.models.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("principal", "period_start", name=op.f("pk_principal_usage")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        for name in ("cache_read_input_tokens", "cache_creation_input_tokens", "cost_microusd"):
            batch_op.add_column(
                sa.Column(name, sa.BigInteger(), server_default="0", nullable=False)
            )
        batch_op.add_column(
            sa.Column("unpriced_calls", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("tokens_reserved", sa.BigInteger(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "budget_period_start", autoswe.models.UTCDateTime(timezone=True), nullable=True
            )
        )
        batch_op.add_column(sa.Column("error_code", sa.String(length=64), nullable=True))
    with op.batch_alter_table("stage_runs", schema=None) as batch_op:
        for name in ("cache_read_input_tokens", "cache_creation_input_tokens", "cost_microusd"):
            batch_op.add_column(
                sa.Column(name, sa.BigInteger(), server_default="0", nullable=False)
            )


def downgrade() -> None:
    with op.batch_alter_table("stage_runs", schema=None) as batch_op:
        for name in ("cost_microusd", "cache_creation_input_tokens", "cache_read_input_tokens"):
            batch_op.drop_column(name)
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        for name in (
            "error_code", "budget_period_start", "tokens_reserved", "unpriced_calls",
            "cost_microusd", "cache_creation_input_tokens", "cache_read_input_tokens",
        ):  # fmt: skip
            batch_op.drop_column(name)
    op.drop_table("principal_usage")
