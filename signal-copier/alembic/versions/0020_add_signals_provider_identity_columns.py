"""Add signals.channel_id/message_id/revision_id (Track 5, point 6:
cross-collector/cross-transport redelivery dedup key)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-30

Nullable, additive columns -- see Signal.channel_id/message_id/
revision_id's own docstrings in app/models.py and
SignalStore.find_signal_id_by_provider_identity's docstring in app/db.py
for exactly what they're for: the provider's own native message identity,
now persisted on every `signals` row (not just carried in-memory on the
`Signal` dataclass and lost once `save_signal` returns), so a second
collector observing the SAME provider event can be recognized as such and
canonicalized onto the same signal id before app/engine.py's own SIG-01
per-signal-id replay-lookup runs.

A fresh database gets these straight from app/db.py's `_COLUMN_MIGRATIONS`
(SignalStore's own bootstrap, not this file -- see 0016's identical
precedent for `signals.import_batch`). This revision only matters for
someone provisioning a database purely through the Alembic CLI, or an
operator running `alembic upgrade head` by hand against an existing
deployment stamped at 0019.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("channel_id", sa.Text(), nullable=True))
    op.add_column("signals", sa.Column("message_id", sa.Text(), nullable=True))
    op.add_column("signals", sa.Column("revision_id", sa.Text(), nullable=True))
    op.create_index(
        "idx_signals_provider_identity", "signals", ["channel_id", "message_id", "revision_id"]
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
