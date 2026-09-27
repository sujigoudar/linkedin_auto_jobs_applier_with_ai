"""add release_reviews table

Revision ID: a5581417bf7f
Revises: db545234a3c1
Create Date: 2026-09-27 21:05:00.000000

The `release_reviews` table (app/models/release_review.py) backing
AD-08 "Release and change approvals". Plain tenant-scoped table, added
to the generic `_TENANT_SCOPED_TABLES` policy in app/db.py -- unlike
`products`, it needs no cross-tenant visibility exception, since a
release review is never publicly visible.

Applies row-level security in the SAME transaction this migration's own
`create_table` runs in (via `op.get_bind()`, not a fresh engine/
connection), the same reason the `04c418cbb547` and `db545234a3c1`
revisions do this.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'a5581417bf7f'
down_revision: str | None = 'db545234a3c1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'release_reviews',
        sa.Column('release_review_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('product_id', sa.String(), nullable=False),
        sa.Column('object_revision_reviewed', sa.Integer(), nullable=False),
        sa.Column('proposer_user_id', sa.String(), nullable=False),
        sa.Column('evidence_manifest_id', sa.String(), nullable=False),
        sa.Column('audience_policy_id', sa.String(), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'state',
            sa.Enum('QUEUED', 'CHANGES_REQUESTED', 'REJECTED', 'APPROVED', name='releasereviewstate', native_enum=False),
            nullable=False,
        ),
        sa.Column('reviewer_user_id', sa.String(), nullable=True),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['product_id'], ['products.product_id']),
        sa.PrimaryKeyConstraint('release_review_id'),
    )
    op.create_index(op.f('ix_release_reviews_tenant_id'), 'release_reviews', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_release_reviews_product_id'), 'release_reviews', ['product_id'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`, per `04c418cbb547`'s own updated
    #: docstring on why that broke a fresh `alembic upgrade head` replay.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("release_reviews",))


def downgrade() -> None:
    op.drop_index(op.f('ix_release_reviews_product_id'), table_name='release_reviews')
    op.drop_index(op.f('ix_release_reviews_tenant_id'), table_name='release_reviews')
    op.drop_table('release_reviews')
