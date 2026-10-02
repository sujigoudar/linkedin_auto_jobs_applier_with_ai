"""Add canonical identity tables for WC-02 (PhysicalAccount, AccountBinding, CapabilityProfile).

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-02 00:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create physical_accounts, account_bindings, and capability_profiles tables."""
    # Physical accounts: immutable, deduplicated broker accounts
    op.create_table(
        "physical_accounts",
        sa.Column("physical_account_id", sa.String(255), primary_key=True),
        sa.Column("broker", sa.String(255), nullable=False),
        sa.Column("broker_account_id", sa.String(255), nullable=False),
        sa.Column(
            "environment",
            sa.String(50),
            nullable=False,
            comment="'paper', 'live', 'sandbox', or 'unknown'",
        ),
        sa.Column(
            "base_currency",
            sa.String(3),
            nullable=False,
            comment="ISO 4217 code (e.g., 'USD', 'EUR')",
        ),
        sa.Column(
            "margin_type",
            sa.String(50),
            nullable=False,
            comment="'cash', 'margin', 'retirement', or 'unknown'",
        ),
        sa.Column(
            "restriction_state",
            sa.String(50),
            nullable=False,
            comment="'none', 'pdt_restricted', 'closing_only', or 'unknown'",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("broker", "broker_account_id", "environment"),
    )

    # Account bindings: credentials/integrations reaching physical accounts
    op.create_table(
        "account_bindings",
        sa.Column("binding_id", sa.String(255), primary_key=True),
        sa.Column("physical_account_id", sa.String(255), nullable=False),
        sa.Column("config_account_id", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("revoked", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["physical_account_id"],
            ["physical_accounts.physical_account_id"],
        ),
        sa.ForeignKeyConstraint(
            ["config_account_id"],
            ["config_accounts.account_id"],
        ),
    )

    op.create_index(
        "ix_account_bindings_physical_account_id",
        "account_bindings",
        ["physical_account_id"],
    )
    op.create_index(
        "ix_account_bindings_config_account_id",
        "account_bindings",
        ["config_account_id"],
    )

    # Capability profiles: exact instrument/operation support with evidence tier
    op.create_table(
        "capability_profiles",
        sa.Column("capability_id", sa.String(255), primary_key=True),
        sa.Column("physical_account_id", sa.String(255), nullable=False),
        sa.Column(
            "instrument_family",
            sa.String(50),
            nullable=False,
            comment="'stock', 'option', 'future', 'fx', 'crypto', etc.",
        ),
        sa.Column(
            "session",
            sa.String(50),
            nullable=False,
            comment="'regular', 'pre', 'after', etc.",
        ),
        sa.Column(
            "operation",
            sa.String(50),
            nullable=False,
            comment="'entry_long', 'entry_short', 'exit', 'stop', 'target', etc.",
        ),
        sa.Column(
            "order_recipe",
            sa.String(50),
            nullable=False,
            comment="'limit', 'market', 'stop_limit', 'algo', etc.",
        ),
        sa.Column(
            "evidence_tier",
            sa.String(50),
            nullable=False,
            comment="'unknown', 'declared', 'simulator', 'paper', or 'live'",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["physical_account_id"],
            ["physical_accounts.physical_account_id"],
        ),
        sa.UniqueConstraint(
            "physical_account_id",
            "instrument_family",
            "session",
            "operation",
        ),
    )

    op.create_index(
        "ix_capability_profiles_physical_account_id",
        "capability_profiles",
        ["physical_account_id"],
    )
    op.create_index(
        "ix_capability_profiles_evidence_tier",
        "capability_profiles",
        ["evidence_tier"],
    )


def downgrade() -> None:
    """Drop identity tables."""
    op.drop_index("ix_capability_profiles_evidence_tier", "capability_profiles")
    op.drop_index(
        "ix_capability_profiles_physical_account_id", "capability_profiles"
    )
    op.drop_table("capability_profiles")

    op.drop_index("ix_account_bindings_config_account_id", "account_bindings")
    op.drop_index("ix_account_bindings_physical_account_id", "account_bindings")
    op.drop_table("account_bindings")

    op.drop_table("physical_accounts")
