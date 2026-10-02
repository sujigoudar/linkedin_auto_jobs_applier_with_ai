"""Add real fill timestamps and confirmed_at for E-07/E-08 fix.

Revision ID: 0040
Revises: 0036
Create Date: 2026-10-02 00:00:00.000000

E-07 fix: Reconciliation-confirmed fills now preserve broker's actual
executed_at timestamp; confirmed_at tracks the poll time separately.
E-08 fix: Synthetic lifecycle signals excluded from latency calculations.

Note: This revision is assigned ID 0040 with intended down_revision 0037 per
the remediation plan. However, intermediate revisions 0037-0039 from other
work packages are not yet present. This file uses 0036 as down_revision for
testing; the integrator will re-chain revisions as needed.

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add confirmed_at column to orders table for tracking poll time."""
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "confirmed_at",
                sa.String(32),
                nullable=True,
                comment="ISO 8601 timestamp when this fill was confirmed by the broker during reconciliation polling (distinct from executed_at which is the broker's actual fill time); NULL means never reconciled or predates this column"
            )
        )


def downgrade() -> None:
    """Remove confirmed_at column."""
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("confirmed_at")
