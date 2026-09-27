"""add portfolio_selections table

Revision ID: f6f6e1fc0793
Revises: 390949c83081
Create Date: 2026-09-27 23:30:00.000000

The `portfolio_selections` table (app/models/portfolio_selection.py)
backing CU-02 "My portfolios" -- a customer's own record of choosing to
copy a published Product.

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
revision: str = 'f6f6e1fc0793'
down_revision: str | None = '390949c83081'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'portfolio_selections',
        sa.Column('selection_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('product_id', sa.String(), nullable=False),
        sa.Column(
            'state', sa.Enum('ACTIVE', 'CANCELLED', name='portfolioselectionstate', native_enum=False), nullable=False
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['tenant_id', 'user_id'],
            ['memberships.tenant_id', 'memberships.user_id'],
            name='fk_portfolio_selection_membership',
        ),
        sa.ForeignKeyConstraint(['product_id'], ['products.product_id']),
        sa.PrimaryKeyConstraint('selection_id'),
    )
    op.create_index(op.f('ix_portfolio_selections_tenant_id'), 'portfolio_selections', ['tenant_id'])
    #: At most one ACTIVE selection per customer per product -- a
    #: cancelled selection is never edited or deleted, so a customer can
    #: cancel and later re-select the same product as a new row.
    op.create_index(
        'uq_portfolio_selection_active_customer_product',
        'portfolio_selections',
        ['tenant_id', 'user_id', 'product_id'],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )

    #: Only the table THIS revision adds -- never the live, ever-growing
    #: `app.db._TENANT_SCOPED_TABLES`.
    connection = op.get_bind()
    _apply_row_level_security(connection, ("portfolio_selections",))


def downgrade() -> None:
    op.drop_index(op.f('ix_portfolio_selections_tenant_id'), table_name='portfolio_selections')
    op.drop_table('portfolio_selections')
