"""add content_documents table

Revision ID: b149fadfc60c
Revises: 18f28ae32fe3
Create Date: 2026-09-28 02:15:00.000000

The `content_documents` table (app/models/content_document.py) backing
AD-19 "Content and disclosure publishing". Plain tenant-scoped table,
no compound FK needed (this is admin content, not a customer's own
record).

Applies row-level security in the SAME transaction this migration's own
`create_table` runs in (via `op.get_bind()`), pinned to only the table
THIS revision adds -- never the live, ever-growing
`app.db._TENANT_SCOPED_TABLES`, per `04c418cbb547`'s own lesson.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = 'b149fadfc60c'
down_revision: str | None = '18f28ae32fe3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'content_documents',
        sa.Column('document_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column(
            'document_type',
            sa.Enum(
                'METHODOLOGY', 'RISK', 'BILLING_TERMS', 'PRIVACY', 'HELP', 'STATUS',
                name='contentdocumenttype', native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('locale', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('body', sa.String(), nullable=False),
        sa.Column('audience_policy_id', sa.String(), nullable=False),
        sa.Column('source_evidence_ids', sa.ARRAY(sa.String()), nullable=False),
        sa.Column(
            'state',
            sa.Enum('DRAFT', 'SUBMITTED_FOR_REVIEW', 'PUBLISHED', name='contentdocumentstate', native_enum=False),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('document_id'),
    )
    op.create_index(op.f('ix_content_documents_tenant_id'), 'content_documents', ['tenant_id'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("content_documents",))


def downgrade() -> None:
    op.drop_index(op.f('ix_content_documents_tenant_id'), table_name='content_documents')
    op.drop_table('content_documents')
