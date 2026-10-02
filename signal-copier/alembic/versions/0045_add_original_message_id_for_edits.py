"""Add original_message_id column to signals table for edit chain tracking.

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add original_message_id column to signals table.

    WP-11 (A-02/A-11): Track the original message id for edit chains.
    When a signal is an edit/revision, this points to the original
    message's message_id. NULL for originals and non-revision-aware signals.
    Used to find all orders produced by the original signal when deciding
    how to amend vs reject/accept an edit.
    """
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "original_message_id",
                sa.Text,
                nullable=True,
                comment="WP-11: The original message_id this revision traces back to (NULL for originals)",
            )
        )


def downgrade() -> None:
    """Remove original_message_id column from signals table."""
    with op.batch_alter_table("signals", schema=None) as batch_op:
        batch_op.drop_column("original_message_id")
