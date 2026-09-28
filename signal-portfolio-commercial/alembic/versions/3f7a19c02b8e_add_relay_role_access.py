"""add relay_role and its restricted access

Revision ID: 3f7a19c02b8e
Revises: 9c2e5b8a1f47
Create Date: 2026-09-28 09:00:00.000000

The Signal Platform Integration Correction Pack's own
INTEGRATION_DECISION.md S6/S8 -- see app/db.py's `_apply_relay_role_access`
docstring for what `relay_role` is and why it needs exactly one bespoke
permissive policy on `export_stream_registrations` and nothing more.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from app.db import _apply_relay_role_access

# revision identifiers, used by Alembic.
revision: str = '3f7a19c02b8e'
down_revision: str | None = '9c2e5b8a1f47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    _apply_relay_role_access(connection)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(text("DROP POLICY IF EXISTS relay_stream_lookup ON export_stream_registrations"))
    connection.execute(text("REVOKE ALL ON export_stream_registrations, inbox_events, ledger_entries FROM relay_role"))
    connection.execute(text("DROP ROLE IF EXISTS relay_role"))
