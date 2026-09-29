"""fix portfolio_selections active-state partial unique index predicate

Revision ID: 01e9924e74c6
Revises: 85f9e0e6c123
Create Date: 2026-09-29 00:00:00.000000

`f6f6e1fc0793` created `uq_portfolio_selection_active_customer_product`
as a partial unique index on (tenant_id, user_id, product_id) WHERE
`state = 'active'`, meant to enforce "at most one ACTIVE selection per
customer per product." But `PortfolioSelectionState.ACTIVE`'s column is
`Enum(PortfolioSelectionState, native_enum=False)`, and SQLAlchemy's
non-native `Enum` binds/stores a member by its NAME ('ACTIVE'), never
its `.value` ('active') -- this codebase's own established convention
(see `7a3f9c1d2e4b`'s evidence_class comment for the precedent). So
every row's actual stored `state` is the literal string 'ACTIVE'
(uppercase), and the index predicate `WHERE state = 'active'` never
matched any row -- it has enforced nothing since it was introduced.

Verified empirically against a real Postgres 16 cluster: inserting two
`portfolio_selections` rows with identical (tenant_id, user_id,
product_id) and `state='ACTIVE'` directly (bypassing the app-level ORM
pre-check in app/services/portfolio_selection.py, which correctly
compares against `PortfolioSelectionState.ACTIVE` and so isn't itself
exposed to this on the normal synchronous path) both succeeded before
this fix, and the second now raises `UniqueViolation` after it.

This was only a backstop gap, not an active leak, since the
application always pre-checks before creating a new selection --  but
a race between two concurrent "select this portfolio" requests for the
same customer+product had zero database-level protection, exactly the
case this index exists to prevent.

Drops and recreates the index with the corrected predicate
`WHERE state = 'ACTIVE'`, matching the value actually stored.
`app/models/portfolio_selection.py`'s `Index(...)` in `__table_args__`
is fixed to match in the same change, so the ORM model and the real
schema stay in sync (otherwise a future `alembic revision
--autogenerate` would flip-flop this forever).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '01e9924e74c6'
down_revision: str | None = '85f9e0e6c123'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index('uq_portfolio_selection_active_customer_product', table_name='portfolio_selections')
    op.create_index(
        'uq_portfolio_selection_active_customer_product',
        'portfolio_selections',
        ['tenant_id', 'user_id', 'product_id'],
        unique=True,
        postgresql_where=sa.text("state = 'ACTIVE'"),
    )


def downgrade() -> None:
    op.drop_index('uq_portfolio_selection_active_customer_product', table_name='portfolio_selections')
    op.create_index(
        'uq_portfolio_selection_active_customer_product',
        'portfolio_selections',
        ['tenant_id', 'user_id', 'product_id'],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
