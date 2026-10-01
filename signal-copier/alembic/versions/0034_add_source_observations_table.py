"""Add the source_observations table (Track 24 -- the generic, transport-
agnostic adapter-boundary record; see app/sources/adapter_contract.py's
own module docstring and app/db.py's `source_observations` CREATE TABLE
comment) and `sources.acquisition_checkpoint` (Track 24's RSS adapter's
own poll checkpoint -- see that column's own comment for why it lives on
`sources` rather than on `collectors`).

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-01

Purely additive -- no existing table is dropped or altered beyond adding
one new nullable column, same precedent as every earlier revision in
this directory. No backfill: nothing in this codebase before this track
recorded a source observation, so this table starts genuinely empty for
an existing database, exactly as it does for a fresh one.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("acquisition_checkpoint", sa.Text(), nullable=True))

    op.create_table(
        "source_observations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("connection_id", sa.Text(), nullable=True),
        sa.Column("provider_id", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Text(), nullable=True),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("source_namespace", sa.Text(), nullable=True),
        sa.Column("original_item_id", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True),
        sa.Column("revision_identifier", sa.Text(), nullable=True),
        sa.Column("observation_kind", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("revision_seq", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_authored_at", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.Text(), nullable=True),
        sa.Column("first_observed_at", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.Text(), nullable=False),
        sa.Column("timestamp_origin", sa.Text(), nullable=True),
        sa.Column("timestamp_uncertain", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completeness", sa.Text(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("attachment_refs", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("adapter_name", sa.Text(), nullable=False),
        sa.Column("backend", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.Text(), nullable=True),
        sa.Column("retrieval_method", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        sa.Column("acquisition_run_id", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False, server_default="research"),
        sa.Column("eligibility_state", sa.Text(), nullable=False, server_default="not_eligible"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_source_observations_source_id", "source_observations", ["source_id", "retrieved_at"])
    op.create_index("idx_source_observations_connection_id", "source_observations", ["connection_id"])
    op.create_index("idx_source_observations_eligibility", "source_observations", ["eligibility_state"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
