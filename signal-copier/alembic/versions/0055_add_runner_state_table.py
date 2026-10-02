"""WC-08: Runner state tracking for pyramiding and staged entries.

Revision ID: 0052
Revises: 0046
Create Date: 2026-10-02 00:00:00.000000

Adds runner_state table to persist runner lifecycle state across restarts,
including high-water marks, giveback rules, deadlines, and monotonic floors
for pyramiding and staged entry mechanisms.

References:
- WORKFLOW_SPECIFICATION.md §13.4 (Runner lifecycle)
- Invariants I11 (monotonic floor), I16 (preserve seed risk), I19 (persist across restart)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runner_state",
        sa.Column("id", sa.Text, nullable=False, primary_key=True),
        sa.Column("account_id", sa.Text, nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("lifecycle_id", sa.Text, nullable=False),
        sa.Column("owned_quantity", sa.Integer, nullable=False),
        sa.Column("entry_price_cents", sa.Integer, nullable=False),
        sa.Column("high_water_cents", sa.Integer, nullable=False),
        sa.Column("giveback_cents", sa.Integer, nullable=False),
        sa.Column("protective_floor_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("original_risk_cents", sa.Integer, nullable=False, server_default="0"),
        sa.Column("deadline_utc", sa.DateTime, nullable=True),
        sa.Column("created_at_utc", sa.DateTime, nullable=False),
        sa.Column("updated_at_utc", sa.DateTime, nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["config_accounts.account_id"]),
        sa.UniqueConstraint("account_id", "symbol", "lifecycle_id", name="uq_runner_state_key"),
    )
    op.create_index("ix_runner_state_account_id", "runner_state", ["account_id"])
    op.create_index("ix_runner_state_symbol", "runner_state", ["symbol"])
    op.create_index("ix_runner_state_lifecycle_id", "runner_state", ["lifecycle_id"])


def downgrade() -> None:
    op.drop_index("ix_runner_state_lifecycle_id", table_name="runner_state")
    op.drop_index("ix_runner_state_symbol", table_name="runner_state")
    op.drop_index("ix_runner_state_account_id", table_name="runner_state")
    op.drop_table("runner_state")
