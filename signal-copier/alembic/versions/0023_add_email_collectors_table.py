"""Add email_collectors table (Track 7: persistent email collector
registry -- see app/email_collectors.py's module docstring)

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-30

See app/db.py's own email_collectors table comment for exactly what each
real column holds and app/email_collectors.py for the registered
vocabulary (ConnectionMode/AllowedUse/CollectorHealth) this table's text
columns are constrained to at the application layer. A fresh database
gets this straight from app/db.py's SCHEMA string (SignalStore's own
bootstrap re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a
pre-existing database picks up the new table too) -- same "this file is
not the real source of truth for a SignalStore-created database"
precedent as 0019_add_telegram_collectors_table.py.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_collectors",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("connection_mode", sa.Text(), nullable=False),
        sa.Column("identity_ref", sa.Text(), nullable=False),
        sa.Column("credential_env_var", sa.Text(), nullable=False),
        sa.Column("imap_host", sa.Text(), nullable=False),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("imap_folder", sa.Text(), nullable=False),
        sa.Column("sender_allowlist", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("subject_patterns", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("allowed_uses", sa.Text(), nullable=False, server_default='["private_trading"]'),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("last_qualified_at", sa.Text(), nullable=True),
        sa.Column("qualification_evidence", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("checkpoint_uid", sa.Integer(), nullable=True),
        sa.Column("checkpoint_updated_at", sa.Text(), nullable=True),
        sa.Column("health_state", sa.Text(), nullable=False, server_default="no_messages_observed"),
        sa.Column("health_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_email_collectors_provider", "email_collectors", ["provider_name"])
    op.create_index("idx_email_collectors_mailbox", "email_collectors", ["imap_host", "imap_folder"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
