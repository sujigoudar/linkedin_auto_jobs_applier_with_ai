"""Add sample-driven parser tooling tables (Track 15 -- see
app/parser_tooling.py's module docstring)

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-30

Creates `parser_samples` and `parser_profiles` (see app/db.py's own
`SCHEMA` comments for each table's exact column list/rationale). Purely
additive -- no existing table is dropped or altered, same precedent as
every earlier revision in this directory. No backfill: unlike 0028,
nothing in this codebase before this track produced data these tables
could be populated from -- both start genuinely empty for an existing
database, exactly as they do for a fresh one.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parser_samples",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=True),
        sa.Column("extracted_fields", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("disposition_outcome", sa.Text(), nullable=True),
        sa.Column("is_corrected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("corrected_message_type", sa.Text(), nullable=True),
        sa.Column("corrected_fields", sa.Text(), nullable=True),
        sa.Column("correction_note", sa.Text(), nullable=True),
        sa.Column("corrected_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_parser_samples_source_id", "parser_samples", ["source_id"])
    op.create_index("idx_parser_samples_provider_id", "parser_samples", ["provider_id"])
    op.create_index("idx_parser_samples_is_corrected", "parser_samples", ["is_corrected"])

    op.create_table(
        "parser_profiles",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("test_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accuracy_metrics", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("supported_message_types", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("fallback_model", sa.Text(), nullable=False, server_default="none"),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("activated_at", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.Text(), nullable=True),
    )
    op.create_index("idx_parser_profiles_provider_id", "parser_profiles", ["provider_id"])
    op.create_index("idx_parser_profiles_status", "parser_profiles", ["status"])
    op.create_index(
        "idx_parser_profiles_provider_active",
        "parser_profiles",
        ["provider_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
