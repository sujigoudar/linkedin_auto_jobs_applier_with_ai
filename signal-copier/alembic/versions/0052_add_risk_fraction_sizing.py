"""WP-16: Add risk_fraction sizing mode for dynamic position sizing.

Revision ID: 0052
Revises: 0044
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add sizing_mode and risk_fraction columns to config_accounts."""
    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "sizing_mode",
                sa.String(),
                nullable=False,
                server_default="multiplier",
                comment="B-01: sizing strategy for this account. One of 'multiplier' (default), 'fixed', or 'risk_fraction'."
            )
        )
        batch_op.add_column(
            sa.Column(
                "risk_fraction",
                sa.Float(),
                nullable=True,
                comment="B-01: for sizing_mode='risk_fraction', the fraction of account equity to risk per trade (e.g., 0.01 for 1%). NULL means this mode is not in use."
            )
        )


def downgrade() -> None:
    """Remove sizing_mode and risk_fraction columns from config_accounts."""
    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.drop_column("risk_fraction")
        batch_op.drop_column("sizing_mode")
