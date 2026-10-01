"""relay_role gets source_stop_target_revisions access

Revision ID: c7e2f91a4d05
Revises: b1f4d8a2c6e3
Create Date: 2026-10-01 00:05:00.000000

Track 41 (ADR-0011): `ingest_export_event`'s own `SOURCE_EVENT` branch
now writes a `SourceStopTargetRevision` row for `TARGET_UPDATE`/
`STOP_UPDATE`, through the SAME restricted relay path that already
needed `INSERT` on `ledger_entries` (migration `3f7a19c02b8e`). This
calls the SEPARATE `_apply_relay_role_source_stop_target_revisions_
access` -- never `_apply_relay_role_access` itself, which is live code
called by that earlier, now-historical migration and would break on
replay against a fresh database if it referenced a table this
migration's own predecessor (`b1f4d8a2c6e3`) is the one that creates.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from app.db import _apply_relay_role_source_stop_target_revisions_access

# revision identifiers, used by Alembic.
revision: str = 'c7e2f91a4d05'
down_revision: str | None = 'b1f4d8a2c6e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    _apply_relay_role_source_stop_target_revisions_access(connection)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("REVOKE INSERT ON source_stop_target_revisions FROM relay_role"))
