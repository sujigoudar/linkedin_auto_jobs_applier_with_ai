"""Add website_collectors and website_article_candidates tables (Track 9:
persistent website/article collector registry -- see
app/website_collectors.py's module docstring)

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-30

See app/db.py's own website_collectors/website_article_candidates table
comments for exactly what each real column holds and
app/website_collectors.py for the registered vocabulary (SiteFormat/
AllowedUse/CollectorHealth) the text columns are constrained to at the
application layer. A fresh database gets these straight from app/db.py's
SCHEMA string (SignalStore's own bootstrap re-runs `CREATE TABLE IF NOT
EXISTS` on every open) -- same "this file is not the real source of
truth for a SignalStore-created database" precedent as 0019's identical
one for telegram_collectors.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "website_collectors",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("site_format", sa.Text(), nullable=False),
        sa.Column("site_id", sa.Text(), nullable=False),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("feed_url", sa.Text(), nullable=True),
        sa.Column("article_list_url", sa.Text(), nullable=True),
        sa.Column("analyst", sa.Text(), nullable=True),
        sa.Column("auth_state_env_var", sa.Text(), nullable=True),
        sa.Column("allowed_uses", sa.Text(), nullable=False, server_default='["private_trading"]'),
        sa.Column("last_qualified_at", sa.Text(), nullable=True),
        sa.Column("qualification_evidence", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("checkpoint_article_url", sa.Text(), nullable=True),
        sa.Column("checkpoint_seen_urls", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("checkpoint_updated_at", sa.Text(), nullable=True),
        sa.Column("health_state", sa.Text(), nullable=False, server_default="unqualified"),
        sa.Column("health_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_website_collectors_provider", "website_collectors", ["provider_name"])
    op.create_index("idx_website_collectors_site", "website_collectors", ["site_id"])

    op.create_table(
        "website_article_candidates",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("signal_id", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("modified_at", sa.Text(), nullable=True),
        sa.Column("candidate_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.UniqueConstraint("channel_id", "message_id", name="uq_website_article_candidates_channel_message"),
    )
    op.create_index("idx_website_article_candidates_channel", "website_article_candidates", ["channel_id"])


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
