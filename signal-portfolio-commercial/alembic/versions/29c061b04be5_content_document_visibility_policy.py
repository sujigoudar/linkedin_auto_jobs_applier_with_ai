"""content_documents gets its own bespoke visibility policy

Revision ID: 29c061b04be5
Revises: 97c4cfe16128
Create Date: 2026-09-28 03:00:00.000000

PU-06 "Methodology, risk and legal documents" needs an anonymous,
no-tenant-scope session to see every tenant's PUBLISHED content
documents, while AD-19's own admin session must still see its own
tenant's rows in any state -- the same problem `products` already
solved with its own bespoke `product_visibility` policy (see
`db545234a3c1`'s own migration). The generic per-tenant `tenant_isolation`
policy this table got from `b149fadfc60c` can't express that, so this
migration swaps it for `content_document_visibility`
(`app.db._apply_content_document_visibility_policy`), applied within
this migration's own already-open transaction. `content_documents` is
removed from the live `app.db._TENANT_SCOPED_TABLES` constant in this
same commit -- this migration only affects the one table it names, not
a fresh `alembic upgrade head` replay of any earlier migration.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from app.db import _apply_content_document_visibility_policy

# revision identifiers, used by Alembic.
revision: str = '29c061b04be5'
down_revision: str | None = '97c4cfe16128'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    _apply_content_document_visibility_policy(connection)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("DROP POLICY IF EXISTS content_document_visibility ON content_documents"))
    connection.execute(
        text(
            "CREATE POLICY tenant_isolation ON content_documents "
            "USING (tenant_id = current_setting('app.tenant_id', true))"
        )
    )
