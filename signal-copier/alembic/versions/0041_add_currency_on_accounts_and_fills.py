"""Add currency tracking to accounts and order fills for multi-currency support.

E-11: No account/base currency anywhere; realized P&L sums quote-currency
units across symbols; export currency is guessed from symbol syntax.

B-10: Per-account, owner-wide and strategy ceilings sum raw numbers across
currencies and instruments; no basis currency.

Fixes:
- Add currency column to config_accounts for account base currency
- Add price_currency column to orders for per-fill price currency
- Never fabricate or guess currency values; NULL is honest when unavailable

Revision ID: 0041
Revises: 0037
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add currency tracking columns."""
    # Add base currency to config_accounts
    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "currency",
                sa.String(3),
                nullable=True,
                comment="Account base currency (ISO 4217 code, e.g., USD). "
                "NULL means not declared; assume USD for backward compatibility "
                "only when reading existing configurations.",
            )
        )

    # Add price currency to orders
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "price_currency",
                sa.String(10),
                nullable=True,
                comment="Currency in which filled_price is quoted (ISO 4217 code, "
                "e.g., USD, JPY, EUR, BTC). NULL for pre-existing rows; never "
                "fabricated from symbol syntax.",
            )
        )


def downgrade() -> None:
    """Remove currency tracking columns."""
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("price_currency")

    with op.batch_alter_table("config_accounts", schema=None) as batch_op:
        batch_op.drop_column("currency")
