"""Add notification_bridge_devices / notification_bridge_events tables
(Track 10: Android NotificationListenerService fallback capture path --
see app/notification_bridge.py's module docstring)

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-30

See app/db.py's own notification_bridge_devices/notification_bridge_events
table comments for exactly what each real column holds and
app/notification_bridge.py for the registered vocabulary
(ContentCompleteness/DeviceHealth) this table's text columns are
constrained to at the application layer. A fresh database gets both
tables straight from app/db.py's SCHEMA string (SignalStore's own
bootstrap re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a
pre-existing database picks them up too) -- same "this file is not the
real source of truth for a SignalStore-created database" precedent as
every earlier numbered revision's own docstring (see
0019_add_telegram_collectors_table.py, the closest sibling: another
per-device/collector registry table added the same way).
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
        "notification_bridge_devices",
        sa.Column("device_id", sa.Text(), primary_key=True),
        sa.Column("pairing_token_hash", sa.Text(), nullable=False),
        sa.Column("app_packages", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("provider_mapping", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_heartbeat_at", sa.Text(), nullable=True),
        sa.Column("recent_completeness", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("health_state", sa.Text(), nullable=False, server_default="never_paired"),
        sa.Column("health_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_table(
        "notification_bridge_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("app_package", sa.Text(), nullable=False),
        sa.Column("notification_key", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("revision_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("content_completeness", sa.Text(), nullable=False),
        sa.Column("posted_at", sa.Text(), nullable=True),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("signal_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index(
        "idx_notification_bridge_events_key", "notification_bridge_events", ["device_id", "notification_key"]
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
