"""WC-06: Durable intents, outbox and SUBMISSION_UNKNOWN recovery.

Revision ID: 0051
Revises: 0046
Create Date: 2026-10-02 00:00:00.000000

Implements §6.3 (SQLite transaction and effect boundary): atomically claim
the opportunity, validate versions, write selection, reserve resources, write
the immutable order intent and outbox item, then commit. A coordinated account
writer marks dispatching in a second transaction, calls the adapter outside
any transaction, then persists the response.

Creates two tables:
- order_intents: immutable record of each order intent with its resources
- outbox: transactional outbox queue for dispatch and crash recovery
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create order_intents and outbox tables."""
    # order_intents: immutable record of each order intent
    op.create_table(
        "order_intents",
        sa.Column("intent_id", sa.String(36), primary_key=True, comment="UUID of this intent"),
        sa.Column("opportunity_id", sa.String(255), nullable=False, comment="Signal/scenario identifier"),
        sa.Column("physical_account_id", sa.String(255), nullable=False, comment="Broker account targeted"),
        sa.Column("binding_id", sa.String(255), nullable=False, comment="Config-account binding ID"),
        sa.Column("client_correlation_id", sa.String(255), nullable=False, comment="Broker idempotency key"),
        sa.Column("policy_hash", sa.String(64), nullable=False, comment="SHA256 of sizing policy"),
        sa.Column("quantity", sa.Integer, nullable=False, comment="Units in instrument step (never rounded up)"),
        sa.Column("price_constraints", sa.Text, nullable=True, comment="JSON dict: entry/exit/stop prices"),
        sa.Column("protection_recipe", sa.Text, nullable=True, comment="JSON dict: stop/target recipe"),
        sa.Column("reservation_id", sa.String(255), nullable=False, comment="FK to budget_reservations"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="When this intent was created",
        ),
    )

    # Index for lookups by opportunity (unique constraint not enforced at DB level)
    op.create_index("ix_order_intents_opportunity", "order_intents", ["opportunity_id"])
    op.create_index("ix_order_intents_account", "order_intents", ["physical_account_id"])
    op.create_index("ix_order_intents_binding", "order_intents", ["binding_id"])
    op.create_index("ix_order_intents_reservation", "order_intents", ["reservation_id"])

    # outbox: transactional queue for dispatch and crash recovery
    op.create_table(
        "outbox",
        sa.Column("item_id", sa.String(36), primary_key=True, comment="UUID of this outbox entry"),
        sa.Column(
            "intent_id",
            sa.String(36),
            sa.ForeignKey("order_intents.intent_id"),
            nullable=False,
            comment="FK to order_intents",
        ),
        sa.Column("state", sa.String(32), nullable=False, default="outboxed", comment="IntentState value"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="When this item was enqueued",
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True, comment="When worker claimed it"),
        sa.Column("claimed_by", sa.String(255), nullable=True, comment="Worker lease ID that claimed it"),
        sa.Column("response", sa.Text, nullable=True, comment="JSON-serialized broker response"),
        sa.Column(
            "response_recorded_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When response was recorded (NULL until recorded)",
        ),
    )

    # Index for queue processing (find oldest unclaimed)
    op.create_index("ix_outbox_state_created", "outbox", ["state", "created_at"])
    op.create_index("ix_outbox_intent", "outbox", ["intent_id"])


def downgrade() -> None:
    """Drop order_intents and outbox tables."""
    op.drop_table("outbox")
    op.drop_table("order_intents")
