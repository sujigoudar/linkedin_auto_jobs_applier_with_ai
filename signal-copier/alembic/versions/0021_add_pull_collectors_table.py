"""Add pull_collectors table (Track 6: Slack/Twitter user-context collector
registry -- see app/collector_registry.py's module docstring)

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-30

See app/db.py's own pull_collectors table comment for exactly what each
real column holds and app/collector_registry.py for the registered
vocabulary (Provider/CollectorHealth) this table's text columns are
constrained to at the application layer. A fresh database gets this
straight from app/db.py's SCHEMA string (SignalStore's own bootstrap
re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a pre-existing
database picks up the new table too) -- same "this file is not the real
source of truth for a SignalStore-created database" precedent as every
earlier numbered revision's own docstring (see
0019_add_telegram_collectors_table.py). down_revision is 0020
(0020_add_signals_provider_identity_columns.py, Track 5's own signals
dedup-key columns) -- the other, already-merged 0020 revision this one
chains after, not 0019 directly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pull_collectors",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("auth_mode", sa.Text(), nullable=False),
        sa.Column("identity_ref", sa.Text(), nullable=False),
        sa.Column("credential_env_var", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("target_label", sa.Text(), nullable=True),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("allowed_uses", sa.Text(), nullable=False, server_default='["private_trading"]'),
        sa.Column("last_qualified_at", sa.Text(), nullable=True),
        sa.Column("qualification_evidence", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("checkpoint", sa.Text(), nullable=True),
        sa.Column("checkpoint_updated_at", sa.Text(), nullable=True),
        sa.Column("health_state", sa.Text(), nullable=False, server_default="unqualified"),
        sa.Column("health_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_pull_collectors_provider", "pull_collectors", ["provider"])
    op.create_index("idx_pull_collectors_target", "pull_collectors", ["target_id"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
