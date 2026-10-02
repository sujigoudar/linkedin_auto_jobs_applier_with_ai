"""WP-50: Add trailing stop and time exit fields to signals.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-02 00:00:00.000000

D-12: Signal-reachable trailing stops and time exits. Adds three optional
fields to the Signal model and database:
- trail_amount: Trailing stop amount (absolute price delta)
- trail_percent: Trailing stop percentage (0-1 scale)
- time_exit_at: Time-based exit datetime

These allow signals from providers to directly configure managed lifecycle
exits without requiring separate API calls or lifecycle state management.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add trail_amount, trail_percent, and time_exit_at columns to signals."""
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trail_amount", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("trail_percent", sa.Float, nullable=True))
        batch_op.add_column(sa.Column("time_exit_at", sa.Text, nullable=True))


def downgrade() -> None:
    """Remove the trailing/time exit columns from signals."""
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_column("trail_amount")
        batch_op.drop_column("trail_percent")
        batch_op.drop_column("time_exit_at")
