"""add membership self-lookup RLS policy (ADR-0009)

Revision ID: f4a2590f17e2
Revises: c1d2e3f4a5b6
Create Date: 2026-09-29 00:00:00.000000

ADR-0009 -- see app/db.py's `_apply_membership_self_lookup_policy`
docstring for the full bootstrap chicken-and-egg problem this solves
(the sign-in/verify-email routes must find the caller's own
`memberships` row by `user_id` before any `app.tenant_id` scope can be
set) and why a narrow, value-scoped, SELECT-only permissive policy is
the fix, mirroring the precedent ADR-0002 set for `relay_role`'s own
bootstrap lookup.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from app.db import _apply_membership_self_lookup_policy

# revision identifiers, used by Alembic.
revision: str = 'f4a2590f17e2'
down_revision: str | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    _apply_membership_self_lookup_policy(connection)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("DROP POLICY IF EXISTS membership_self_lookup ON memberships"))
