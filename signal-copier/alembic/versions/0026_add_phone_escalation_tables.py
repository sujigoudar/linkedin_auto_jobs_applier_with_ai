"""Add phone_escalation_configs / phone_escalation_attempts tables
(Track 13: escalation-only active phone-control retrieval -- see
app/phone_escalation.py's module docstring)

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-30

See app/db.py's own phone_escalation_configs/phone_escalation_attempts
table comments for exactly what each real column holds and
app/phone_escalation.py for the registered vocabulary
(CapabilityState/EscalationDisposition/ExtractionStatus) these tables'
text columns are constrained to at the application layer. A fresh
database gets both tables straight from app/db.py's SCHEMA string
(SignalStore's own bootstrap re-runs `CREATE TABLE IF NOT EXISTS` on
every open, so a pre-existing database picks them up too) -- same "this
file is not the real source of truth for a SignalStore-created database"
precedent as every earlier numbered revision's own docstring (see
0024_add_notification_bridge_tables.py, the closest sibling: another
per-provider/device registry pair added the same way).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "phone_escalation_configs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("app_package", sa.Text(), nullable=False, unique=True),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("adapter_backend", sa.Text(), nullable=True),
        sa.Column("capability_state", sa.Text(), nullable=False, server_default="disabled"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_phone_escalation_configs_app_package", "phone_escalation_configs", ["app_package"]
    )
    op.create_table(
        "phone_escalation_attempts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("app_package", sa.Text(), nullable=False),
        sa.Column("notification_key", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("capability_state_at_attempt", sa.Text(), nullable=False),
        sa.Column("disposition", sa.Text(), nullable=False),
        sa.Column("extraction_status", sa.Text(), nullable=True),
        sa.Column("extraction_detail", sa.Text(), nullable=True),
        sa.Column("signal_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_phone_escalation_attempts_device", "phone_escalation_attempts", ["device_id", "notification_key"]
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
