"""portfolio_versions: unique (tenant_id, portfolio_id, version_number)

Revision ID: a2c7e4f91b35
Revises: 69ace9c2543a
Create Date: 2026-10-01 00:00:00.000000

Closes a race documented against app/models/portfolio_version.py's own
docstring ("Historical membership is never overwritten") and
app/services/candidate_comparison.py::create_portfolio_version_draft_from_candidate:
that function previously computed `version_number = max(existing) + 1`
with no uniqueness constraint or lock, so two concurrent draft-creation
calls for the same `(tenant_id, portfolio_id)` could both compute and
insert the same `version_number` -- two different rows silently
claiming the same historical slot, which is exactly what "never
overwritten" is supposed to rule out. A plain two-column
`(portfolio_id, version_number)` constraint would be enough on its own
to prevent the collision (a version_number only has to be unique within
one portfolio's own version lineage), but `tenant_id` is included too
so the constraint is enforceable by a single index scan under RLS
without relying on `portfolio_id` values never colliding across
tenants.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a2c7e4f91b35'
down_revision: str | None = '69ace9c2543a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT_NAME = "uq_portfolio_versions_tenant_portfolio_version_number"


def upgrade() -> None:
    op.create_unique_constraint(
        _CONSTRAINT_NAME,
        "portfolio_versions",
        ["tenant_id", "portfolio_id", "version_number"],
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "portfolio_versions", type_="unique")
