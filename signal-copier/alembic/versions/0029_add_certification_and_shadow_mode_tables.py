"""Add certification_checks / shadow_mode_results tables (Track 17:
provider certification + shadow mode -- see app/certification.py's and
app/shadow_mode.py's own module docstrings)

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-30

See app/db.py's own certification_checks/shadow_mode_results table
comments for exactly what each real column holds. Both are genuinely
NEW concepts with no pre-existing table to backfill from (unlike 0028's
own provider/source/connection backfill) -- a fresh database gets both
tables straight from app/db.py's SCHEMA string (SignalStore's own
bootstrap re-runs `CREATE TABLE IF NOT EXISTS` on every open, so a
pre-existing database picks them up too) -- same "this file is not the
real source of truth for a SignalStore-created database" precedent as
every earlier numbered revision's own docstring (see 0026's own
docstring, the closest sibling: another pair of tables added the same
way with nothing to backfill).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "certification_checks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("asset_class", sa.Text(), nullable=False),
        sa.Column("account_route", sa.Text(), nullable=False),
        sa.Column("check_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="NOT_RUN"),
        sa.Column("evidence", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("checked_at", sa.Text(), nullable=True),
        sa.Column("checked_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "provider_id", "source_id", "asset_class", "account_route", "check_name",
            name="uq_certification_checks_scope_check",
        ),
    )
    op.create_index(
        "idx_certification_checks_scope",
        "certification_checks",
        ["provider_id", "source_id", "asset_class", "account_route"],
    )
    op.create_table(
        "shadow_mode_results",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("signal_id", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("expected_entry", sa.Float(), nullable=True),
        sa.Column("stop_price", sa.Float(), nullable=True),
        sa.Column("targets", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("policy_reference", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False, server_default=""),
        sa.Column("computed_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_shadow_mode_results_signal_id", "shadow_mode_results", ["signal_id"])
    op.create_index("idx_shadow_mode_results_provider_id", "shadow_mode_results", ["provider_id"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
