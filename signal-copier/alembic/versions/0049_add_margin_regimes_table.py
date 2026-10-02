"""WC-09: Add physical_accounts and margin_regimes tables for margin regime tracking.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-02 00:00:00.000000

Spec §9: Store per-physical-account margin regime (legacy_pdt_verified |
new_intraday_verified | unknown) with evidence and date. Unknown regime blocks
affected new exposure (I17). FINRA replacement intraday-margin standards are
effective 2026-06-04 with phase-in through 2027-10-20 per account/evidence.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create physical_accounts and margin_regimes tables."""
    # Physical accounts table (WC-02 stub for WC-09 foreign key reference)
    op.create_table(
        "physical_accounts",
        sa.Column(
            "physical_account_id",
            sa.String(255),
            primary_key=True,
            comment="Canonical account identifier (broker + broker_account_id or config_account_id with evidence tier)",
        ),
        sa.Column(
            "broker",
            sa.String(255),
            nullable=False,
            comment="Broker name (alpaca, ibkr, oanda, etc.)",
        ),
        sa.Column(
            "broker_account_id",
            sa.String(255),
            nullable=True,
            comment="Broker-reported account ID; NULL means unmapped (evidence_tier=declared)",
        ),
        sa.Column(
            "environment",
            sa.String(50),
            nullable=True,
            comment="Execution environment: paper|live|sandbox|unknown",
        ),
        sa.Column(
            "base_currency",
            sa.String(3),
            nullable=False,
            comment="ISO 4217 currency code (USD, EUR, JPY, etc.)",
        ),
        sa.Column(
            "margin_type",
            sa.String(50),
            nullable=True,
            comment="Account margin type: cash|margin|retirement|unknown",
        ),
        sa.Column(
            "restriction_state",
            sa.String(50),
            nullable=True,
            comment="Trading restriction: none|pdt_restricted|closing_only|unknown",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_physical_accounts_broker", "physical_accounts", ["broker"])

    # Margin regimes table (WC-09 main table)
    op.create_table(
        "margin_regimes",
        sa.Column(
            "physical_account_id",
            sa.String(255),
            sa.ForeignKey("physical_accounts.physical_account_id"),
            primary_key=True,
            comment="Reference to physical_accounts; one regime per account",
        ),
        sa.Column(
            "regime",
            sa.String(50),
            nullable=False,
            comment="Margin regime: legacy_pdt_verified | new_intraday_verified | unknown. Unknown blocks new exposure (I17).",
        ),
        sa.Column(
            "evidence",
            sa.Text,
            nullable=False,
            comment="Broker evidence/description (required for owner API; >= 3 chars for new via PUT)",
        ),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="Timestamp when regime was verified (UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_margin_regimes_regime", "margin_regimes", ["regime"])


def downgrade() -> None:
    """Drop margin_regimes and physical_accounts tables."""
    op.drop_table("margin_regimes")
    op.drop_table("physical_accounts")
