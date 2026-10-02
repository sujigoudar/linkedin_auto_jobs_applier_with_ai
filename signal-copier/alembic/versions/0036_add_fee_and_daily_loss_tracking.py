"""Add fee tracking and daily loss limit columns for financial correctness.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add fee tracking and daily loss columns."""
    # Add fee tracking to orders table
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fee", sa.Numeric(precision=18, scale=8), nullable=True, comment="Broker commission/fee for this order (in account currency)"))
        batch_op.add_column(sa.Column("fee_currency", sa.String(3), nullable=True, comment="Currency of fee (ISO 4217 code, e.g., USD)"))
        batch_op.add_column(sa.Column("slippage", sa.Numeric(precision=18, scale=8), nullable=True, comment="Difference between expected and actual fill price (* quantity)"))

    # Add daily loss limit to config_accounts table
    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("daily_loss_limit_percent", sa.Numeric(precision=5, scale=2), nullable=True, comment="Maximum acceptable daily loss as percentage of equity (e.g., 5 for 5%; NULL=disabled)")
        )
        batch_op.add_column(
            sa.Column("min_equity_threshold", sa.Numeric(precision=18, scale=8), nullable=True, comment="Minimum equity threshold; new entries rejected if breached (account currency; NULL=disabled)")
        )

    # Create daily_pnl tracking table
    op.create_table(
        "daily_pnl",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.String(255), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("opening_equity", sa.Numeric(precision=18, scale=8), nullable=True, comment="Equity at start of trading day"),
        sa.Column("closing_equity", sa.Numeric(precision=18, scale=8), nullable=True, comment="Equity at end of trading day or last update"),
        sa.Column("realized_pnl", sa.Numeric(precision=18, scale=8), nullable=True, comment="Realized P&L from closed positions"),
        sa.Column("unrealized_pnl", sa.Numeric(precision=18, scale=8), nullable=True, comment="Unrealized P&L from open positions"),
        sa.Column("fees", sa.Numeric(precision=18, scale=8), nullable=True, comment="Total fees/commissions for the day"),
        sa.Column("slippage", sa.Numeric(precision=18, scale=8), nullable=True, comment="Total slippage for the day"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "date", name="uq_daily_pnl_account_date"),
    )

    # Create margin call alerts table
    op.create_table(
        "margin_call_alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("account_id", sa.String(255), nullable=False, index=True),
        sa.Column("alert_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("current_equity", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("maintenance_requirement", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("excess_margin", sa.Numeric(precision=18, scale=8), nullable=False, comment="Negative if margin call"),
        sa.Column("broker", sa.String(255), nullable=False),
        sa.Column("resolved", sa.Boolean, default=False, nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    """Remove fee tracking and daily loss columns."""
    op.drop_table("margin_call_alerts")
    op.drop_table("daily_pnl")

    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.drop_column("daily_loss_limit_percent")
        batch_op.drop_column("min_equity_threshold")

    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("fee")
        batch_op.drop_column("fee_currency")
        batch_op.drop_column("slippage")
