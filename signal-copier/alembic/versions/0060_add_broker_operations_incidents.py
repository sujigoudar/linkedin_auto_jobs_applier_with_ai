"""Add broker_operations_incidents table (AgentMail incident escalation, Task #194).

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-03 00:00:00.000000

Mirrors the CREATE TABLE in app/db.py's bootstrap schema so the CLI-only
`alembic upgrade head` path and SignalStore's own bootstrap produce the same
tables (enforced by tests/test_e01_alembic_migration_stamping.py).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broker_operations_incidents",
        sa.Column("incident_id", sa.Text, primary_key=True),
        sa.Column("inbox_id", sa.Text, nullable=False),
        sa.Column("account_owner_id", sa.Text, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("severity", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("sender", sa.Text, nullable=False),
        sa.Column("extracted_account", sa.Text, nullable=True),
        sa.Column("extracted_amount", sa.Text, nullable=True),
        sa.Column("extracted_deadline", sa.Text, nullable=True),
        sa.Column("is_duplicate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duplicate_of_incident_id", sa.Text, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False),
    )
    op.create_index(
        "idx_broker_operations_incidents_account_owner_id",
        "broker_operations_incidents",
        ["account_owner_id"],
    )
    op.create_index(
        "idx_broker_operations_incidents_created_at",
        "broker_operations_incidents",
        ["created_at"],
    )
    op.create_index(
        "idx_broker_operations_incidents_severity",
        "broker_operations_incidents",
        ["severity"],
    )


def downgrade() -> None:
    op.drop_table("broker_operations_incidents")
