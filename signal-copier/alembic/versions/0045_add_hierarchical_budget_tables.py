"""Add hierarchical budget tables for WC-03.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-02 00:00:00.000000

Implements WORKFLOW_SPECIFICATION.md §5.3–5.4, §6–6.3 with hierarchical
capital allocation, resource vectors, and reservation state machine.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create hierarchical budget tables."""
    # Portfolios: top-level capital grouping
    op.create_table(
        "portfolios",
        sa.Column("portfolio_id", sa.String(255), primary_key=True),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("name", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_portfolios_owner", "portfolios", ["owner"])

    # Portfolio backings: dedicated equity per account
    op.create_table(
        "portfolio_backings",
        sa.Column("backing_id", sa.String(255), primary_key=True),
        sa.Column("portfolio_id", sa.String(255), nullable=False),
        sa.Column("physical_account_id", sa.String(255), nullable=False),
        sa.Column(
            "dedicated_equity_cents",
            sa.Integer,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("portfolio_id", "physical_account_id", name="uq_backing_portfolio_account"),
    )
    op.create_foreign_key(
        "fk_portfolio_backings_portfolio_id",
        "portfolio_backings",
        "portfolios",
        ["portfolio_id"],
        ["portfolio_id"],
    )
    op.create_index("idx_portfolio_backings_portfolio", "portfolio_backings", ["portfolio_id"])
    op.create_index("idx_portfolio_backings_account", "portfolio_backings", ["physical_account_id"])

    # Strategy sleeves: capital subdivisions within a portfolio
    op.create_table(
        "strategy_sleeves",
        sa.Column("sleeve_id", sa.String(255), primary_key=True),
        sa.Column("portfolio_id", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(255), nullable=False),
        sa.Column("analyst", sa.String(255), nullable=True),
        sa.Column("name", sa.String(500), nullable=True),
        sa.Column(
            "max_notional_cents",
            sa.Integer,
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_strategy_sleeves_portfolio_id",
        "strategy_sleeves",
        "portfolios",
        ["portfolio_id"],
        ["portfolio_id"],
    )
    op.create_index("idx_strategy_sleeves_portfolio", "strategy_sleeves", ["portfolio_id"])
    op.create_index("idx_strategy_sleeves_provider", "strategy_sleeves", ["provider"])

    # Budget reservations: per-opportunity state machine (§6.2)
    op.create_table(
        "budget_reservations",
        sa.Column("reservation_id", sa.String(255), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.String(255),
            nullable=False,
            unique=True,
        ),
        sa.Column("owner", sa.String(255), nullable=False),
        sa.Column("physical_account_id", sa.String(255), nullable=False),
        sa.Column("portfolio_id", sa.String(255), nullable=True),
        sa.Column("sleeve_id", sa.String(255), nullable=True),
        sa.Column("provider", sa.String(255), nullable=False),
        sa.Column("analyst", sa.String(255), nullable=True),
        sa.Column("underlying", sa.String(255), nullable=False),
        sa.Column("cluster_id", sa.String(255), nullable=True),
        sa.Column(
            "needed_cash_cents",
            sa.Integer,
            nullable=False,
        ),
        sa.Column(
            "needed_margin_cents",
            sa.Integer,
            nullable=False,
        ),
        sa.Column(
            "needed_notional_cents",
            sa.Integer,
            nullable=False,
        ),
        sa.Column(
            "needed_planned_risk_cents",
            sa.Integer,
            nullable=False,
        ),
        sa.Column(
            "needed_stress_risk_cents",
            sa.Integer,
            nullable=True,
        ),
        sa.Column(
            "state",
            sa.String(50),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "evidence",
            sa.Text,
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_budget_reservations_portfolio_id",
        "budget_reservations",
        "portfolios",
        ["portfolio_id"],
        ["portfolio_id"],
    )
    op.create_foreign_key(
        "fk_budget_reservations_sleeve_id",
        "budget_reservations",
        "strategy_sleeves",
        ["sleeve_id"],
        ["sleeve_id"],
    )
    op.create_index("idx_budget_reservations_owner", "budget_reservations", ["owner"])
    op.create_index("idx_budget_reservations_account", "budget_reservations", ["physical_account_id"])
    op.create_index("idx_budget_reservations_state", "budget_reservations", ["state"])
    op.create_index("idx_budget_reservations_opportunity", "budget_reservations", ["opportunity_id"])

    # Owner-level limits on aggregate exposure
    op.create_table(
        "owner_limits",
        sa.Column("limit_id", sa.String(255), primary_key=True),
        sa.Column(
            "owner",
            sa.String(255),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "max_notional_cents",
            sa.Integer,
            nullable=True,
        ),
        sa.Column(
            "max_planned_risk_cents",
            sa.Integer,
            nullable=True,
        ),
        sa.Column(
            "max_stress_risk_cents",
            sa.Integer,
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_owner_limits_owner", "owner_limits", ["owner"])


def downgrade() -> None:
    """Drop hierarchical budget tables."""
    op.drop_table("owner_limits")
    op.drop_table("budget_reservations")
    op.drop_table("strategy_sleeves")
    op.drop_table("portfolio_backings")
    op.drop_table("portfolios")
