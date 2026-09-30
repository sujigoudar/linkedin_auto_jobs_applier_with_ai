"""Add Track 16's signal-lifecycle timeline columns, provider freshness
config columns, and conflict-resolution-policy columns.

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-30

See app/signal_freshness.py's own module docstring for the freshness
config columns' full contract (`providers.max_add_age_seconds`/
`adjustment_stale_behavior`/`timestamp_source_preference`/
`clock_skew_tolerance_seconds`/`recovered_event_behavior` -- Track 14
already reserved `max_entry_age_seconds`/`stale_exit_policy`/
`correlation_window_seconds`, this revision adds the rest the user's own
spec named), app/signal_correlation.py's own `ConflictResolutionPolicy`
docstring for `providers.conflict_resolution_policy`/
`deterministic_primary_source_id`/`correlation_price_tolerance_pct`, and
app/models.py's `Signal` docstrings for the five new `signals` timeline
columns (`source_created_at`/`source_modified_at`/`first_observed_at`/
`parsed_at`/`decision_at` -- `received_at` already existed).

Every new column is nullable (or, for `conflict_resolution_policy`,
defaults to `'HOLD'` -- the conservative floor, Track 12's own original
behavior) -- this revision can never change what an existing signal/
provider/source that never sets any of these does. A fresh database gets
all of this straight from app/db.py's `SCHEMA`/`_COLUMN_MIGRATIONS` (same
"this file is not the real source of truth for a SignalStore-created
database" precedent as every earlier revision here); this migration only
matters for `alembic upgrade head` against an existing deployment
stamped at 0028.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("source_created_at", sa.Text(), nullable=True))
    op.add_column("signals", sa.Column("source_modified_at", sa.Text(), nullable=True))
    op.add_column("signals", sa.Column("first_observed_at", sa.Text(), nullable=True))
    op.add_column("signals", sa.Column("parsed_at", sa.Text(), nullable=True))
    op.add_column("signals", sa.Column("decision_at", sa.Text(), nullable=True))

    op.add_column("providers", sa.Column("max_add_age_seconds", sa.Integer(), nullable=True))
    op.add_column("providers", sa.Column("adjustment_stale_behavior", sa.Text(), nullable=True))
    op.add_column("providers", sa.Column("timestamp_source_preference", sa.Text(), nullable=True))
    op.add_column("providers", sa.Column("clock_skew_tolerance_seconds", sa.Integer(), nullable=True))
    op.add_column("providers", sa.Column("recovered_event_behavior", sa.Text(), nullable=True))
    op.add_column(
        "providers",
        sa.Column("conflict_resolution_policy", sa.Text(), nullable=False, server_default="HOLD"),
    )
    op.add_column("providers", sa.Column("deterministic_primary_source_id", sa.Text(), nullable=True))
    op.add_column("providers", sa.Column("correlation_price_tolerance_pct", sa.Float(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
