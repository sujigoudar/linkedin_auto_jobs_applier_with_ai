"""Add Track 20's mobile-device metadata/allowed-blocked-apps columns to
notification_bridge_devices, and the new mobile_app_configs table (the
"Settings -> Mobile Devices -> Signal Phone -> Apps -> Whop" backend).

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-30

See app/notification_bridge.py's own `NotificationBridgeDevice`/
`MobileAppConfig` docstrings for the full, field-by-field contract these
columns follow, and app/db.py's own `notification_bridge_devices`/
`mobile_app_configs` table comments for exactly what each one holds.

Every new `notification_bridge_devices` column is nullable (or, for
`allowed_apps`/`blocked_apps`, defaults to `'[]'`) -- this revision can
never change what an existing device that has never reported this
metadata looks like; see that dataclass's own docstring for why each one
stays honestly `None`/empty until the Android app itself reports it. A
fresh database gets all of this straight from app/db.py's `SCHEMA`/
`_COLUMN_MIGRATIONS` (same "this file is not the real source of truth
for a SignalStore-created database" precedent as every earlier revision
here); this migration only matters for `alembic upgrade head` against an
existing deployment stamped at 0031.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notification_bridge_devices", sa.Column("device_name", sa.Text(), nullable=True))
    op.add_column("notification_bridge_devices", sa.Column("platform", sa.Text(), nullable=True))
    op.add_column("notification_bridge_devices", sa.Column("model", sa.Text(), nullable=True))
    op.add_column("notification_bridge_devices", sa.Column("os_version", sa.Text(), nullable=True))
    op.add_column("notification_bridge_devices", sa.Column("agent_version", sa.Text(), nullable=True))
    op.add_column("notification_bridge_devices", sa.Column("network_status", sa.Text(), nullable=True))
    op.add_column("notification_bridge_devices", sa.Column("battery_level", sa.Integer(), nullable=True))
    op.add_column("notification_bridge_devices", sa.Column("is_charging", sa.Integer(), nullable=True))
    op.add_column(
        "notification_bridge_devices", sa.Column("notification_permission_granted", sa.Integer(), nullable=True)
    )
    op.add_column(
        "notification_bridge_devices", sa.Column("accessibility_permission_granted", sa.Integer(), nullable=True)
    )
    op.add_column(
        "notification_bridge_devices", sa.Column("screen_control_capability", sa.Integer(), nullable=True)
    )
    op.add_column("notification_bridge_devices", sa.Column("ai_agent_capability", sa.Integer(), nullable=True))
    op.add_column(
        "notification_bridge_devices",
        sa.Column("allowed_apps", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "notification_bridge_devices",
        sa.Column("blocked_apps", sa.Text(), nullable=False, server_default="[]"),
    )

    op.create_table(
        "mobile_app_configs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("device_id", sa.Text(), nullable=False),
        sa.Column("package_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("capture_notifications", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active_retrieval_allowed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retrieval_mode", sa.Text(), nullable=False, server_default="notification_only"),
        sa.Column("notification_title_patterns", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("conversation_patterns", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("expected_screens", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("navigation_recipe", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("ai_fallback_allowed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_navigation_steps", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("screenshot_retention", sa.Text(), nullable=False, server_default="none"),
        sa.Column("content_extraction_schema", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("device_id", "package_name"),
    )
    op.create_index("idx_mobile_app_configs_device", "mobile_app_configs", ["device_id"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
