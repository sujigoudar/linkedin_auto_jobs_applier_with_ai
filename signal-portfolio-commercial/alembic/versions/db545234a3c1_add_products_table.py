"""add products table

Revision ID: db545234a3c1
Revises: 04c418cbb547
Create Date: 2026-09-27 19:00:00.000000

The `products` table (app/models/product.py) backing AD-07 "Products and
portfolio versions" / PU-02 "Portfolio catalog" -- the first dashboard
vertical slice. Applies its own bespoke row-level-security policy
(app/db.py's `_apply_product_visibility_policy`) in the SAME transaction
this migration's own `create_table` runs in (via `op.get_bind()`, not a
fresh engine/connection) -- the same reason the previous
`04c418cbb547_row_level_security_and_append_only_` revision does this:
a new connection would open a separate transaction that can't see this
revision's own not-yet-committed table.

Deliberately NOT part of `_TENANT_SCOPED_TABLES`'s generic per-table
policy (see app/db.py's own docstring on `_apply_product_visibility_policy`):
a `products` row must be visible to its own tenant in any lifecycle
state, AND to every anonymous/no-scope session once PUBLISHED -- a
single uniform "only this tenant" policy can't express both.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

from app.db import _apply_product_visibility_policy

# revision identifiers, used by Alembic.
revision: str = 'db545234a3c1'
down_revision: str | None = '04c418cbb547'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'products',
        sa.Column('product_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('product_name', sa.String(), nullable=False),
        sa.Column('slug', sa.String(), nullable=False),
        sa.Column('portfolio_version_id', sa.String(), nullable=True),
        sa.Column('cash_bps', sa.Integer(), nullable=False),
        sa.Column('service_modes', sa.ARRAY(sa.String()), nullable=False),
        sa.Column('audience_policy_id', sa.String(), nullable=True),
        sa.Column('research_report_id', sa.String(), nullable=True),
        sa.Column('methodology_document_id', sa.String(), nullable=True),
        sa.Column(
            'lifecycle_state',
            sa.Enum('DRAFT', 'VALIDATED', 'APPROVED', 'PUBLISHED', name='productlifecyclestate', native_enum=False),
            nullable=False,
        ),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['portfolio_version_id'], ['portfolio_versions.portfolio_version_id']),
        sa.PrimaryKeyConstraint('product_id'),
        sa.UniqueConstraint('slug'),
    )
    op.create_index(op.f('ix_products_tenant_id'), 'products', ['tenant_id'], unique=False)

    connection = op.get_bind()
    _apply_product_visibility_policy(connection)


def downgrade() -> None:
    op.drop_index(op.f('ix_products_tenant_id'), table_name='products')
    op.drop_table('products')
