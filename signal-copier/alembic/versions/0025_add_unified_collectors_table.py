"""Add the unified collectors table (Track 8: unified collector-registry
layer -- see app/unified_collectors.py's module docstring)

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-30

Creates the `collectors` table (see app/db.py's own `collectors` table
comment in its `SCHEMA` string for exactly what each column holds) and
BACKFILLS it from the four pre-existing per-provider registry tables
that were migrated onto it: `telegram_collectors`, `pull_collectors`,
`email_collectors`, `website_collectors`. Each old row is copied across
losslessly -- every field it held is either a shared column on the new
table or moved into that row's own `provider_config` JSON blob (see
app/unified_collectors.py for the exact field mapping per kind) -- so no
existing collector's identity, credentials-reference, qualification
evidence, checkpoint, or health history is lost.

`notification_bridge_devices` (Track 10) is DELIBERATELY NOT migrated
here -- see app/unified_collectors.py's module docstring for why its
shape doesn't fit this table (no allowed_uses/qualification_evidence
concept, a hashed pairing token rather than a credential_env_var
reference, and read-time health-state overrides tied to heartbeat
staleness with no equivalent elsewhere). It keeps its own dedicated
table, unchanged.

The four OLD tables (telegram_collectors/pull_collectors/
email_collectors/website_collectors) are intentionally NOT dropped by
this migration. `app/db.py`'s `SignalStore` no longer writes to them as
of this Track (its `register_telegram_collector`/`register_pull_
collector`/`register_email_collector`/`register_website_collector` and
every other CRUD method for these four now read/write the unified
`collectors` table exclusively -- see app/unified_collectors.py), but
dropping them outright in the same migration that introduces their
replacement is exactly the kind of "believe the new path is correct and
delete the old data in the same breath" mistake a real production
migration should not make. Keeping them (now unused, effectively frozen
historical snapshots as of this migration's backfill) costs nothing but
disk, and gives a real rollback path if the unified table's design turns
out to need revision -- a later migration can drop them once the unified
table has been running in production long enough to trust. See this
same "downgrade is not supported, but don't be reckless about deletes
either" precedent already established by every earlier revision in this
directory (0001's own docstring).

A fresh database gets the `collectors` table straight from
`app/db.py`'s `SCHEMA` string (`SignalStore`'s own bootstrap re-runs
`CREATE TABLE IF NOT EXISTS` on every open) -- same precedent as every
earlier numbered revision's own docstring (see
0019_add_telegram_collectors_table.py). This migration's backfill only
matters for an EXISTING database (one opened via alembic upgrade against
data written before this Track), where a fresh `CREATE TABLE IF NOT
EXISTS` alone would leave the new table permanently empty.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "collectors",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("identity_ref", sa.Text(), nullable=True),
        sa.Column("credential_env_var", sa.Text(), nullable=True),
        sa.Column("provider_name", sa.Text(), nullable=True),
        sa.Column("allowed_uses", sa.Text(), nullable=False, server_default='["private_trading"]'),
        sa.Column("last_qualified_at", sa.Text(), nullable=True),
        sa.Column("qualification_evidence", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("checkpoint", sa.Text(), nullable=True),
        sa.Column("checkpoint_updated_at", sa.Text(), nullable=True),
        sa.Column("health_state", sa.Text(), nullable=False, server_default="unqualified"),
        sa.Column("health_detail", sa.Text(), nullable=True),
        sa.Column("provider_config", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_collectors_kind", "collectors", ["kind"])
    op.create_index("idx_collectors_kind_provider", "collectors", ["kind", "provider"])

    bind = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    _backfill_telegram(bind, now)
    _backfill_pull(bind, now)
    _backfill_email(bind, now)
    _backfill_website(bind, now)


def _insert_collector(bind, row: dict) -> None:
    bind.execute(
        sa.text(
            """INSERT INTO collectors
                   (id, kind, provider, identity_ref, credential_env_var, provider_name, allowed_uses,
                    last_qualified_at, qualification_evidence, checkpoint, checkpoint_updated_at,
                    health_state, health_detail, provider_config, created_at, updated_at)
               VALUES
                   (:id, :kind, :provider, :identity_ref, :credential_env_var, :provider_name, :allowed_uses,
                    :last_qualified_at, :qualification_evidence, :checkpoint, :checkpoint_updated_at,
                    :health_state, :health_detail, :provider_config, :created_at, :updated_at)
               ON CONFLICT(id) DO NOTHING"""
        ),
        row,
    )


def _backfill_telegram(bind, now: str) -> None:
    rows = bind.execute(
        sa.text(
            """SELECT id, connection_mode, identity_ref, credential_env_var, chat_id, topic_id, provider_name,
                      allowed_uses, noforwards, last_qualified_at, qualification_evidence, checkpoint_message_id,
                      checkpoint_updated_at, health_state, health_detail, created_at, updated_at
               FROM telegram_collectors"""
        )
    ).fetchall()
    for r in rows:
        provider_config = {
            "chat_id": r[4],
            "topic_id": r[5],
            "noforwards": bool(r[8]) if r[8] is not None else None,
        }
        _insert_collector(
            bind,
            {
                "id": r[0],
                "kind": "telegram",
                "provider": r[1],
                "identity_ref": r[2],
                "credential_env_var": r[3],
                "provider_name": r[6],
                "allowed_uses": r[7] or '["private_trading"]',
                "last_qualified_at": r[9],
                "qualification_evidence": r[10] or "{}",
                "checkpoint": json.dumps(r[11]) if r[11] is not None else None,
                "checkpoint_updated_at": r[12],
                "health_state": r[13],
                "health_detail": r[14],
                "provider_config": json.dumps(provider_config),
                "created_at": r[15] or now,
                "updated_at": r[16] or now,
            },
        )


def _backfill_pull(bind, now: str) -> None:
    rows = bind.execute(
        sa.text(
            """SELECT id, provider, auth_mode, identity_ref, credential_env_var, target_id, target_label,
                      provider_name, allowed_uses, last_qualified_at, qualification_evidence, checkpoint,
                      checkpoint_updated_at, health_state, health_detail, created_at, updated_at
               FROM pull_collectors"""
        )
    ).fetchall()
    for r in rows:
        provider_config = {"auth_mode": r[2], "target_id": r[5], "target_label": r[6]}
        _insert_collector(
            bind,
            {
                "id": r[0],
                "kind": "pull",
                "provider": r[1],
                "identity_ref": r[3],
                "credential_env_var": r[4],
                "provider_name": r[7],
                "allowed_uses": r[8] or '["private_trading"]',
                "last_qualified_at": r[9],
                "qualification_evidence": r[10] or "{}",
                "checkpoint": json.dumps(r[11]) if r[11] is not None else None,
                "checkpoint_updated_at": r[12],
                "health_state": r[13],
                "health_detail": r[14],
                "provider_config": json.dumps(provider_config),
                "created_at": r[15] or now,
                "updated_at": r[16] or now,
            },
        )


def _backfill_email(bind, now: str) -> None:
    rows = bind.execute(
        sa.text(
            """SELECT id, connection_mode, identity_ref, credential_env_var, imap_host, imap_port, imap_folder,
                      sender_allowlist, subject_patterns, provider_name, allowed_uses, poll_interval_seconds,
                      last_qualified_at, qualification_evidence, checkpoint_uid, checkpoint_updated_at,
                      health_state, health_detail, created_at, updated_at
               FROM email_collectors"""
        )
    ).fetchall()
    for r in rows:
        provider_config = {
            "imap_host": r[4],
            "imap_port": r[5],
            "imap_folder": r[6],
            "sender_allowlist": json.loads(r[7]) if r[7] else [],
            "subject_patterns": json.loads(r[8]) if r[8] else [],
            "poll_interval_seconds": r[11],
        }
        _insert_collector(
            bind,
            {
                "id": r[0],
                "kind": "email",
                "provider": r[1],
                "identity_ref": r[2],
                "credential_env_var": r[3],
                "provider_name": r[9],
                "allowed_uses": r[10] or '["private_trading"]',
                "last_qualified_at": r[12],
                "qualification_evidence": r[13] or "{}",
                "checkpoint": json.dumps(r[14]) if r[14] is not None else None,
                "checkpoint_updated_at": r[15],
                "health_state": r[16],
                "health_detail": r[17],
                "provider_config": json.dumps(provider_config),
                "created_at": r[18] or now,
                "updated_at": r[19] or now,
            },
        )


def _backfill_website(bind, now: str) -> None:
    rows = bind.execute(
        sa.text(
            """SELECT id, site_format, site_id, provider_name, feed_url, article_list_url, analyst,
                      auth_state_env_var, allowed_uses, last_qualified_at, qualification_evidence,
                      checkpoint_article_url, checkpoint_seen_urls, checkpoint_updated_at, health_state,
                      health_detail, created_at, updated_at
               FROM website_collectors"""
        )
    ).fetchall()
    for r in rows:
        provider_config = {
            "site_id": r[2],
            "feed_url": r[4],
            "article_list_url": r[5],
            "analyst": r[6],
            "auth_state_env_var": r[7],
            "checkpoint_seen_urls": json.loads(r[12]) if r[12] else [],
        }
        _insert_collector(
            bind,
            {
                "id": r[0],
                "kind": "website",
                "provider": r[1],
                "identity_ref": None,
                "credential_env_var": None,
                "provider_name": r[3],
                "allowed_uses": r[8] or '["private_trading"]',
                "last_qualified_at": r[9],
                "qualification_evidence": r[10] or "{}",
                "checkpoint": json.dumps(r[11]) if r[11] is not None else None,
                "checkpoint_updated_at": r[13],
                "health_state": r[14],
                "health_detail": r[15],
                "provider_config": json.dumps(provider_config),
                "created_at": r[16] or now,
                "updated_at": r[17] or now,
            },
        )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
