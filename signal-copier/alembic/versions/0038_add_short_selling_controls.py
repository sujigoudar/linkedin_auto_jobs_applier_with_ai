"""Add allow_short field to config_accounts and Side.SHORT to signal side enum.

Revision ID: 0038
Revises: 0036
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add allow_short column to config_accounts table."""
    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "allow_short",
                sa.Integer,
                nullable=False,
                server_default="0",
                comment="B-14: Whether this account is allowed to open short positions (0=false, 1=true)",
            )
        )


def downgrade() -> None:
    """Remove allow_short column from config_accounts table."""
    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.drop_column("allow_short")
