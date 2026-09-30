"""Add the Provider/Source/Connection catalog tables (Track 14 -- see
app/provider_catalog.py's module docstring)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-30

Creates `providers`, `sources`, `connections` (see app/db.py's own
`SCHEMA` comments for each table's exact column list) and BACKFILLS them
from every existing collector/registry source of truth this track was
asked to unify a view over, without touching or dropping any of it:

- `collectors` (Track 8's unified table, itself already backfilled from
  the four original per-registry tables by 0025): each row becomes ONE
  `providers` row (keyed by `provider`, deduplicated -- two `collectors`
  rows with the same `provider` string share one `providers` row, since
  that string IS this codebase's existing provider identity, e.g.
  `Signal.source`) and ONE `sources` row (`role='PRIMARY'` -- a
  reasonable default since the pre-Track-14 data model had no role
  concept at all, so every existing collector was, in effect, treated as
  its provider's one and only capture path). Its `credential_env_var`/
  `identity_ref` become a new `connections` row
  (`connection_type=f"legacy_{kind}"`, since `collectors.kind` is the
  closest existing analogue to a transport-family label,
  `credential_reference=credential_env_var` -- already an env-var name,
  never a raw secret, matching this track's own hard rule).

- `notification_bridge_devices` (Track 10/12): each device becomes ONE
  `connections` row (`connection_type='android_notification'`,
  `credential_reference` pointing at this row's own `pairing_token_hash`
  -- already a hash, never re-derived or duplicated -- `capabilities`
  reflecting what Track 10/13 already track: `push=True` (notifications
  arrive pushed, never polled), `health_check=True` (heartbeat exists)).
  Each `app_package` entry in that device's `provider_mapping` becomes
  its own `sources` row (`role='FALLBACK'` -- a reasonable default per
  the Track 14 brief's own suggestion: notification-bridge capture is
  typically a fallback path for a provider primarily reached another way
  -- an operator can promote a specific one to PRIMARY after the fact via
  `SignalStore.update_provider_catalog_entry`/direct `sources` update, this
  migration does not guess which). An `app_package` with a `"rules"` list
  (the Track 12 Whop shape -- many providers behind one package) becomes
  ONE `sources` row per distinct `provider_name` named in those rules
  (plus the package's own top-level `provider_name`, if set and not
  already covered by a rule) -- each `providers` row created/reused by
  `provider_name`, `sources.source_native_id` recording
  `"{app_package}:{title_pattern_or_blank}"` so which rule a `sources`
  row came from stays inspectable. A package with NEITHER a
  `provider_name` nor any `"rules"` entries falls back to the bare
  `app_package` as both the provider identity and `source_native_id` --
  same "honest, undecorated default" convention `app/notification_
  bridge.py`'s own `resolve_provider_mapping` already uses at read time.

- `phone_escalation_configs` (Track 13): NOT separately backfilled here
  by design -- see app/provider_catalog.py's own docstring section on
  how this table relates to `sources`/`connections` (its `app_package`
  key is an independent, string-matched reference to the same real-world
  device/package this migration's `notification_bridge_devices` backfill
  already represents as `sources.source_native_id`/`connections.id`, not
  a formal FK this migration introduces).

Providers created by this migration for a `provider` string this
database has never seen before are named honestly from what already
exists (the `provider`/`provider_name` string itself) -- NEVER a
fabricated display name, description, classification, or asset-class
list. Every field this migration cannot derive from real existing data
(`classification`, `asset_classes`, `strategy_types`,
`default_parser_profile`, `risk_policy_ref`, ...) is left NULL/empty,
for an operator to fill in later via the new CRUD/REST surface.

No existing table is dropped or altered -- same precedent as every
earlier revision in this directory (0025's own docstring).

A fresh database gets these three tables straight from `app/db.py`'s
`SCHEMA` string; this migration's backfill only matters for an EXISTING
database opened via `alembic upgrade` against data written before this
track.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("aliases", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="onboarding"),
        sa.Column("account_ownership", sa.Text(), nullable=True),
        sa.Column("subscription_status", sa.Text(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=True),
        sa.Column("asset_classes", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("strategy_types", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("provider_timezone", sa.Text(), nullable=True),
        sa.Column("execution_eligibility", sa.Text(), nullable=False, server_default="disabled"),
        sa.Column("default_parser_profile", sa.Text(), nullable=True),
        sa.Column("max_entry_age_seconds", sa.Integer(), nullable=True),
        sa.Column("stale_exit_policy", sa.Text(), nullable=True),
        sa.Column("min_parse_confidence", sa.Float(), nullable=True),
        sa.Column("correlation_window_seconds", sa.Integer(), nullable=True),
        sa.Column("risk_policy_ref", sa.Text(), nullable=True),
        sa.Column("certification_state", sa.Text(), nullable=False, server_default="uncertified"),
        sa.Column("certification_version", sa.Text(), nullable=True),
        sa.Column("certified_at", sa.Text(), nullable=True),
        sa.Column("operator_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_providers_status", "providers", ["status"])

    op.create_table(
        "sources",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=True),
        sa.Column("source_native_id", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("url_or_reference", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("role", sa.Text(), nullable=False, server_default="PRIMARY"),
        sa.Column("capture_method", sa.Text(), nullable=True),
        sa.Column("connection_id", sa.Text(), nullable=True),
        sa.Column("parser_profile", sa.Text(), nullable=True),
        sa.Column("asset_classes", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("strategy_types", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("freshness_policy", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("dedup_policy", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("execution_eligibility", sa.Text(), nullable=False, server_default="disabled"),
        sa.Column("health_state", sa.Text(), nullable=False, server_default="unqualified"),
        sa.Column("last_event_at", sa.Text(), nullable=True),
        sa.Column("last_success_at", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_sources_provider_id", "sources", ["provider_id"])
    op.create_index("idx_sources_connection_id", "sources", ["connection_id"])

    op.create_table(
        "connections",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("connection_type", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("credential_reference", sa.Text(), nullable=True),
        sa.Column("authentication_type", sa.Text(), nullable=True),
        sa.Column("account_identity", sa.Text(), nullable=True),
        sa.Column("connection_state", sa.Text(), nullable=False, server_default="unconfigured"),
        sa.Column("authorization_state", sa.Text(), nullable=False, server_default="unauthorized"),
        sa.Column("scopes", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("capabilities", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("rate_limits", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("cost_info", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("last_authenticated_at", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.Text(), nullable=True),
        sa.Column("last_heartbeat_at", sa.Text(), nullable=True),
        sa.Column("last_successful_event_at", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.Text(), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.Column("retry_state", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("health_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_connections_connection_type", "connections", ["connection_type"])

    bind = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    _backfill_from_collectors(bind, now)
    _backfill_from_notification_bridge(bind, now)


def _insert_provider_if_absent(bind, provider_id: str, display_name: str, now: str) -> None:
    bind.execute(
        sa.text(
            """INSERT INTO providers (id, display_name, aliases, asset_classes, strategy_types, status,
                                       execution_eligibility, certification_state, created_at, updated_at)
               VALUES (:id, :display_name, '[]', '[]', '[]', 'onboarding', 'disabled', 'uncertified', :now, :now)
               ON CONFLICT(id) DO NOTHING"""
        ),
        {"id": provider_id, "display_name": display_name, "now": now},
    )


def _insert_source(bind, row: dict) -> None:
    bind.execute(
        sa.text(
            """INSERT INTO sources
                   (id, provider_id, platform, source_type, source_native_id, display_name, enabled, priority,
                    role, capture_method, connection_id, execution_eligibility, health_state, created_at, updated_at)
               VALUES
                   (:id, :provider_id, :platform, :source_type, :source_native_id, :display_name, 1, 100,
                    :role, :capture_method, :connection_id, 'disabled', :health_state, :created_at, :updated_at)
               ON CONFLICT(id) DO NOTHING"""
        ),
        row,
    )


def _insert_connection(bind, row: dict) -> None:
    bind.execute(
        sa.text(
            """INSERT INTO connections
                   (id, connection_type, display_name, credential_reference, connection_state,
                    authorization_state, capabilities, created_at, updated_at)
               VALUES
                   (:id, :connection_type, :display_name, :credential_reference, :connection_state,
                    :authorization_state, :capabilities, :created_at, :updated_at)
               ON CONFLICT(id) DO NOTHING"""
        ),
        row,
    )


def _backfill_from_collectors(bind, now: str) -> None:
    """One `providers` row per distinct `collectors.provider` string, one
    `sources` row per `collectors` row (role=PRIMARY), one `connections`
    row per `collectors` row whose `credential_env_var`/`identity_ref` is
    worth recording (a row with neither still gets a connection row, just
    an honest one with a NULL `credential_reference`, since every source
    should be traceable to SOME connection)."""
    rows = bind.execute(
        sa.text(
            """SELECT id, kind, provider, identity_ref, credential_env_var, provider_name, health_state,
                      created_at, updated_at
               FROM collectors"""
        )
    ).fetchall()
    for r in rows:
        collector_id, kind, provider, identity_ref, credential_env_var, provider_name, health_state, created_at, updated_at = r
        display_name = provider_name or provider
        _insert_provider_if_absent(bind, provider, display_name, now)

        connection_id = f"legacy-collector-{collector_id}"
        _insert_connection(
            bind,
            {
                "id": connection_id,
                "connection_type": f"legacy_{kind}",
                "display_name": f"{display_name} ({kind})",
                "credential_reference": credential_env_var,
                "connection_state": "connected" if credential_env_var or identity_ref else "unconfigured",
                "authorization_state": "authorized" if credential_env_var else "unauthorized",
                "capabilities": json.dumps({}),
                "created_at": created_at or now,
                "updated_at": updated_at or now,
            },
        )

        source_health = "healthy" if health_state == "healthy_qualified" else "unqualified"
        _insert_source(
            bind,
            {
                "id": f"legacy-collector-{collector_id}",
                "provider_id": provider,
                "platform": kind,
                "source_type": kind,
                "source_native_id": identity_ref,
                "display_name": display_name,
                "role": "PRIMARY",
                "capture_method": kind,
                "connection_id": connection_id,
                "health_state": source_health,
                "created_at": created_at or now,
                "updated_at": updated_at or now,
            },
        )


def _backfill_from_notification_bridge(bind, now: str) -> None:
    """One `connections` row per `notification_bridge_devices` row. One
    `sources` row per distinct provider named in that device's
    `provider_mapping` (top-level `provider_name` plus every rule's own
    `provider_name`), role=FALLBACK -- see this migration's own module
    docstring for why. A device with no `provider_mapping` at all still
    gets one `sources` row per `app_package`, falling back to the bare
    package name as the provider identity -- same undecorated-default
    convention `resolve_provider_mapping` uses at read time."""
    rows = bind.execute(
        sa.text(
            """SELECT device_id, app_packages, provider_mapping, pairing_token_hash, last_heartbeat_at,
                      health_state, created_at, updated_at
               FROM notification_bridge_devices"""
        )
    ).fetchall()
    for r in rows:
        device_id, app_packages_json, provider_mapping_json, pairing_token_hash, last_heartbeat_at, health_state, created_at, updated_at = r
        app_packages = json.loads(app_packages_json) if app_packages_json else []
        provider_mapping = json.loads(provider_mapping_json) if provider_mapping_json else {}

        connection_id = f"legacy-notification-bridge-{device_id}"
        _insert_connection(
            bind,
            {
                "id": connection_id,
                "connection_type": "android_notification",
                "display_name": f"Notification bridge device {device_id}",
                "credential_reference": f"hash:{pairing_token_hash}" if pairing_token_hash else None,
                "connection_state": "connected" if last_heartbeat_at else "unconfigured",
                "authorization_state": "authorized" if pairing_token_hash else "unauthorized",
                "capabilities": json.dumps({"push": True, "health_check": True, "active_retrieval": False}),
                "created_at": created_at or now,
                "updated_at": updated_at or now,
            },
        )

        source_health = "healthy" if health_state == "healthy_qualified" else "unqualified"
        for app_package in app_packages:
            entry = provider_mapping.get(app_package, {})
            rules = entry.get("rules") or []
            provider_names_seen: set[str] = set()

            top_level_provider = entry.get("provider_name")
            candidates: list[tuple[str, str]] = []
            for rule in rules:
                rule_provider = rule.get("provider_name")
                if rule_provider:
                    title_pattern = rule.get("title_pattern") or ""
                    candidates.append((rule_provider, title_pattern))
            if top_level_provider:
                candidates.append((top_level_provider, ""))
            if not candidates:
                candidates.append((app_package, ""))

            for provider_name, title_pattern in candidates:
                if provider_name in provider_names_seen:
                    continue
                provider_names_seen.add(provider_name)
                _insert_provider_if_absent(bind, provider_name, provider_name, now)
                native_id = f"{app_package}:{title_pattern}" if title_pattern else app_package
                source_id = f"legacy-notification-bridge-{device_id}-{app_package}-{provider_name}"
                _insert_source(
                    bind,
                    {
                        "id": source_id,
                        "provider_id": provider_name,
                        "platform": "notification_bridge",
                        "source_type": "android_notification",
                        "source_native_id": native_id,
                        "display_name": f"{provider_name} via {app_package}",
                        "role": "FALLBACK",
                        "capture_method": "android_notification",
                        "connection_id": connection_id,
                        "health_state": source_health,
                        "created_at": created_at or now,
                        "updated_at": updated_at or now,
                    },
                )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
