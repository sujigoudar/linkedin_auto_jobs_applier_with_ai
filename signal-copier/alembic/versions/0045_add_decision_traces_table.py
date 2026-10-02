"""Add decision_traces table for admission/selection traceability (WC-05).

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
    """Create decision_traces table."""
    op.create_table(
        "decision_traces",
        sa.Column(
            "id",
            sa.String(255),
            primary_key=True,
            comment="Unique trace ID (UUID or signal_id:account_id pair)",
        ),
        sa.Column(
            "signal_id",
            sa.String(255),
            nullable=False,
            comment="Signal that triggered this admission/selection",
        ),
        sa.Column(
            "physical_account_id",
            sa.String(255),
            nullable=False,
            comment="Physical account this candidate record is for",
        ),
        sa.Column(
            "candidate_rank",
            sa.Integer,
            nullable=False,
            comment="Ranking position (lower=better) if feasible, else NULL",
        ),
        sa.Column(
            "feasible",
            sa.Integer,
            nullable=False,
            comment="1 if account can buy minimum size, 0 otherwise (boolean)",
        ),
        sa.Column(
            "reason",
            sa.String(255),
            nullable=False,
            comment="Inclusion reason (if feasible) or exclusion reason (if not)",
        ),
        sa.Column(
            "selected",
            sa.Integer,
            nullable=False,
            comment="1 if this account was selected, 0 otherwise (boolean)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Indexes for decision trace queries
    op.create_index("ix_decision_traces_signal_id", "decision_traces", ["signal_id"])
    op.create_index(
        "ix_decision_traces_account_id", "decision_traces", ["physical_account_id"]
    )


def downgrade() -> None:
    """Drop decision_traces table."""
    op.drop_table("decision_traces")
