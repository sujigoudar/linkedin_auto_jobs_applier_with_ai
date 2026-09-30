"""Add telegram_collectors table (Track 5: persistent Telegram collector
registry -- see app/telegram_collectors.py's module docstring)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-30

See app/db.py's own telegram_collectors table comment for exactly what
each real column holds and app/telegram_collectors.py for the registered
vocabulary (ConnectionMode/AllowedUse/CollectorHealth) this table's text
columns are constrained to at the application layer. A fresh database
gets this straight from app/db.py's SCHEMA string (SignalStore's own
bootstrap re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a
pre-existing database picks up the new table too) -- same "this file is
not the real source of truth for a SignalStore-created database"
precedent as every earlier numbered revision's own docstring (see
0013_add_route_qualifications_table.py).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_collectors",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("connection_mode", sa.Text(), nullable=False),
        sa.Column("identity_ref", sa.Text(), nullable=False),
        sa.Column("credential_env_var", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.Text(), nullable=False),
        sa.Column("topic_id", sa.Text(), nullable=True),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("allowed_uses", sa.Text(), nullable=False, server_default='["private_trading"]'),
        sa.Column("noforwards", sa.Integer(), nullable=True),
        sa.Column("last_qualified_at", sa.Text(), nullable=True),
        sa.Column("qualification_evidence", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("checkpoint_message_id", sa.Integer(), nullable=True),
        sa.Column("checkpoint_updated_at", sa.Text(), nullable=True),
        sa.Column("health_state", sa.Text(), nullable=False, server_default="unqualified"),
        sa.Column("health_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_telegram_collectors_provider", "telegram_collectors", ["provider_name"])
    op.create_index("idx_telegram_collectors_chat", "telegram_collectors", ["chat_id"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
