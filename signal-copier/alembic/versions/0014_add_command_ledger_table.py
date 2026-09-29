"""Add command_ledger table (P0-2, external release audit: one durable
pre-effect command ledger for every entry/close/stop_change/replace/
cancel/flatten)

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-29

See app/db.py's own `command_ledger` table comment for the full contract,
app/command_ledger.py for the real call sites that write/read it, and
app/models.py's `CommandType`/`UncertaintyState` for the closed value sets
`command_type`/`uncertainty_state` take. A fresh database gets this
straight from app/db.py's SCHEMA string (SignalStore's own bootstrap
re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a pre-existing
database picks up the new table too) -- same "this file is not the real
source of truth for a SignalStore-created database" precedent as every
earlier revision's own docstring.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "command_ledger",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("intent_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("command_type", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("expected_revision", sa.Text(), nullable=True),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("remote_identifiers", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("uncertainty_state", sa.Text(), nullable=False),
        sa.Column("terminal_evidence", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("resolved_at", sa.Text(), nullable=True),
    )
    op.create_index("idx_command_ledger_account_id", "command_ledger", ["account_id"])
    op.create_index("idx_command_ledger_created_at", "command_ledger", ["created_at"])
    op.create_index(
        "idx_command_ledger_unresolved", "command_ledger", ["resolved_at"], sqlite_where=sa.text("resolved_at IS NULL")
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
