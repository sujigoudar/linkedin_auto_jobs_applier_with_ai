"""WP-15b: Add contract_multiplier column to orders for notional calculations.

Revision ID: 0051
Revises: 0043
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add contract_multiplier column to orders for tracking contract-specific scaling.

    Used by capital_allocator to scale notional calculations for options, futures, and FX.
    NULL for pre-existing rows (multiplier=1.0 equivalent); new rows populate from
    contract_multiplier(signal).
    """
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "contract_multiplier",
                sa.Float(),
                nullable=True,
                comment="Contract multiplier from contract_multiplier(signal) for notional scaling (options: 100.0, futures: contract-specific, FX: unit-derived, equity/crypto: 1.0)"
            )
        )


def downgrade() -> None:
    """Remove contract_multiplier column from orders."""
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("contract_multiplier")
