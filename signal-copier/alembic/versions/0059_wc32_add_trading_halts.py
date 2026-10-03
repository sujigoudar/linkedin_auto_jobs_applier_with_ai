"""WC-32: Add trading_halts table for risk control and entry blocking.

Revision ID: 0059
Revises: 0058
Create Date: 2026-10-02 00:00:00.000000

WC-32 implementation: track active trading halts at account/portfolio/owner scope,
enabling the admission gate to reject new entries when risk controls are triggered.
Halts can be set by the owner, system, or daily loss limiter, and cleared manually.
Includes index for active halt lookup by scope and scope_id.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create trading_halts table with scope-scoped active halt index."""
    op.create_table(
        "trading_halts",
        sa.Column("halt_id", sa.String(255), primary_key=True),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("scope_id", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by", sa.String(255), nullable=True),
        sa.CheckConstraint(
            "scope IN ('account', 'portfolio', 'owner')",
            name="ck_trading_halts_scope",
        ),
    )
    # Index for active halt lookup (cleared_at IS NULL)
    op.create_index(
        "ix_trading_halts_active",
        "trading_halts",
        ["scope", "scope_id", "cleared_at"],
    )


def downgrade() -> None:
    """Remove trading_halts table."""
    op.drop_table("trading_halts")
