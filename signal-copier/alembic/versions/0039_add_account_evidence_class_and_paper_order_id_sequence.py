"""Add per-account evidence class and paper order ID sequence persistence (WP-38)

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-02

WP-38: copier side of commercial seam addressing G-C-24, G-C-25, G-C-13:
- G-C-24: paper order IDs unique across restarts by persisting sequence per account
- G-C-25: per-account routing outcome with new outcome values (eligible_not_selected, skipped)
- G-C-13: evidence_class per account instead of global RELAY_EVIDENCE_CLASS

Adds two nullable columns to config_accounts:
- evidence_class: TEXT, the EvidenceClass value for this account's exports
- paper_order_id_sequence: INTEGER, monotonic counter for paper broker order IDs
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_accounts", sa.Column("evidence_class", sa.String(), nullable=True))
    op.add_column("config_accounts", sa.Column("paper_order_id_sequence", sa.Integer(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
