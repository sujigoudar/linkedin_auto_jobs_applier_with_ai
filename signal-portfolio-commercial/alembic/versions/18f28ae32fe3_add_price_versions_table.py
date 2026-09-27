"""add price_versions table

Revision ID: 18f28ae32fe3
Revises: 38c97c922bb0
Create Date: 2026-09-28 01:45:00.000000

The `price_versions` table (app/models/price_version.py) backing
AD-13 "Pricing, entitlements and billing operations". Plain tenant-
scoped table, `sku` globally unique like Product.slug's own precedent.

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
revision: str = '18f28ae32fe3'
down_revision: str | None = '38c97c922bb0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'price_versions',
        sa.Column('price_version_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('sku', sa.String(), nullable=False),
        sa.Column('currency', sa.String(), nullable=False),
        sa.Column('amount_minor', sa.Integer(), nullable=False),
        sa.Column(
            'interval', sa.Enum('MONTH', 'YEAR', name='billinginterval', native_enum=False), nullable=False
        ),
        sa.Column('is_unlimited_portfolios', sa.Boolean(), nullable=False),
        sa.Column('portfolio_limit', sa.Integer(), nullable=True),
        sa.Column('features', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('mode', sa.Enum('TEST', 'LIVE', name='pricemode', native_enum=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('price_version_id'),
        sa.UniqueConstraint('sku', name='uq_price_versions_sku'),
    )
    op.create_index(op.f('ix_price_versions_tenant_id'), 'price_versions', ['tenant_id'], unique=False)

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("price_versions",))


def downgrade() -> None:
    op.drop_index(op.f('ix_price_versions_tenant_id'), table_name='price_versions')
    op.drop_table('price_versions')
