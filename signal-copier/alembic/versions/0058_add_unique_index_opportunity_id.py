"""WC-31: Add unique index on order_intents(opportunity_id).

Revision ID: 0058
Revises: 0056
Create Date: 2026-10-02 00:00:00.000000

WC-31 implementation: enforce one order intent per opportunity_id by adding
a unique index. A second intent for the same opportunity will raise an
IntegrityError, preventing duplicate order intents.
"""
from __future__ import annotations

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add unique index on order_intents(opportunity_id), replacing the non-unique index."""
    # Drop the non-unique index if it exists (created by 0053)
    op.execute("DROP INDEX IF EXISTS ix_order_intents_opportunity")
    # Create the unique index
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_order_intents_opportunity "
        "ON order_intents(opportunity_id)"
    )


def downgrade() -> None:
    """Remove unique index from order_intents(opportunity_id) and restore non-unique index."""
    op.execute("DROP INDEX IF EXISTS ux_order_intents_opportunity")
    # Restore the non-unique index
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_order_intents_opportunity "
        "ON order_intents(opportunity_id)"
    )
