"""ALLOC-01: allocation_intents table and routing-rule delivery_mode.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-02 00:00:00.000000

Existing routing rules default to delivery_mode='single' (one selected
account per intended trade). A rule that must keep fan-out semantics has
to be set to 'replicate' explicitly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("config_routing_rules", schema=None) as batch_op:
        batch_op.add_column(sa.Column("delivery_mode", sa.Text, nullable=False, server_default="single"))
    op.create_table(
        "allocation_intents",
        sa.Column("intent_id", sa.Text, primary_key=True),
        sa.Column("signal_id", sa.Text, nullable=False, unique=True),
        sa.Column("strategy_key", sa.Text, nullable=False),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),
        sa.Column("candidates", sa.Text, nullable=False),
        sa.Column("selected_account_id", sa.Text),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("trace", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_index("idx_allocation_intents_state", "allocation_intents", ["state"])
    with op.batch_alter_table("capital_reservations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("strategy_key", sa.Text))
    op.create_table(
        "strategy_budgets",
        sa.Column("strategy_key", sa.Text, primary_key=True),
        sa.Column("max_notional", sa.Float),
        sa.Column("updated_at", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("strategy_budgets")
    with op.batch_alter_table("capital_reservations", schema=None) as batch_op:
        batch_op.drop_column("strategy_key")
    op.drop_index("idx_allocation_intents_state", table_name="allocation_intents")
    op.drop_table("allocation_intents")
    with op.batch_alter_table("config_routing_rules", schema=None) as batch_op:
        batch_op.drop_column("delivery_mode")
