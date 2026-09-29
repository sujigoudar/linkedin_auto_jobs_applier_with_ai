"""SQLAlchemy engine/session setup for the commercial service's own
Postgres database. Entirely separate from `signal-copier/app/db.py`'s
SQLite execution store -- this process never opens that file and that
process never opens this one.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app import config


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str | None = None):
    return create_engine(database_url or config.COMMERCIAL_DATABASE_URL, future=True)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


#: Tables carrying a `tenant_id` column that must never be readable across
#: tenants, even by the table owner -- `FORCE ROW LEVEL SECURITY` is what
#: makes that hold true for the table owner too (plain `ENABLE ROW LEVEL
#: SECURITY` alone is bypassed by the owner and by superusers, which would
#: make this a no-op against the very role these tables are usually queried
#: through in this single-role-per-database development setup).
_TENANT_SCOPED_TABLES: tuple[str, ...] = (
    "memberships", "customer_profiles", "ledger_entries", "sleeves", "subscriptions", "portfolio_versions",
    "release_reviews", "research_runs", "eligibility_assessments", "support_cases", "publisher_destinations",
    "api_keys", "integration_configurations", "price_versions", "managed_programs",
    "audit_events", "workspace_settings", "portfolio_selections", "notification_preferences",
    "customer_display_preferences", "platform_connections", "copy_mandates",
    "export_stream_registrations", "inbox_events", "incidents",
)

#: Tables that must never be UPDATEd or DELETEd from, only appended to (see
#: app/models/ledger.py) -- a mistaken entry is corrected by inserting a new
#: row, never by editing or removing the original. portfolio_versions/
#: portfolio_version_sleeves are append-only for the same reason
#: (app/models/portfolio_version.py): "Historical membership is never
#: overwritten" -- a weight change is a new version's rows, never an edit.
_APPEND_ONLY_TABLES: tuple[str, ...] = (
    "ledger_entries", "portfolio_versions", "portfolio_version_sleeves", "audit_events",
)


def _apply_row_level_security(conn, tables: tuple[str, ...] = _TENANT_SCOPED_TABLES) -> None:
    """`tables` defaults to the CURRENT `_TENANT_SCOPED_TABLES` -- correct
    for a fresh schema (tests/conftest.py's `db_session` fixture, which
    always creates every table before calling this) or `enable_row_level_
    security` below. An Alembic migration that ran before a later table
    existed must instead pass its OWN frozen snapshot of table names
    explicitly (see e.g. `04c418cbb547`'s own upgrade()) -- never the
    live, ever-growing module constant, which would silently reference a
    table that doesn't exist yet when that historical migration replays
    from scratch (caught for real: a fresh `alembic upgrade head` run
    failed with `UndefinedTable` once `release_reviews` was added here
    and this function still defaulted every caller to the current
    tuple)."""
    for table in tables:
        conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
        conn.execute(
            text(
                f"CREATE POLICY tenant_isolation ON {table} "
                "USING (tenant_id = current_setting('app.tenant_id', true))"
            )
        )


def enable_row_level_security(engine) -> None:
    """Enable and FORCE row-level security on every tenant-scoped table, with
    a policy that only ever permits rows matching the session's
    `app.tenant_id` setting -- and permits none at all when that setting is
    unset (fail-closed: no scope set means no visibility, not "everything").

    Idempotent: safe to call every time the schema is (re)created. Opens
    its own connection/transaction on `engine` -- for a caller (e.g. an
    Alembic migration) that must apply this within an ALREADY-OPEN
    transaction/connection that hasn't committed yet (so a fresh
    connection wouldn't see its uncommitted DDL), call
    `_apply_row_level_security(connection)` directly instead."""
    with engine.begin() as conn:
        _apply_row_level_security(conn)


def _apply_append_only(conn, tables: tuple[str, ...] = _APPEND_ONLY_TABLES) -> None:
    """`tables` defaults to the CURRENT `_APPEND_ONLY_TABLES` -- correct
    for a fresh schema or `enforce_append_only` below. An Alembic
    migration that ran before a later append-only table existed must
    instead pass its OWN frozen snapshot of table names explicitly (see
    `04c418cbb547`'s own upgrade()), for the exact same reason
    `_apply_row_level_security` takes this parameter -- a fresh
    `alembic upgrade head` replay would otherwise try to create a
    trigger on a table that doesn't exist yet at that point in
    migration history."""
    conn.execute(
        text(
            "CREATE OR REPLACE FUNCTION forbid_ledger_mutation() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'this table is append-only: % is not permitted', TG_OP; "
            "END; $$ LANGUAGE plpgsql"
        )
    )
    for table in tables:
        conn.execute(text(f"DROP TRIGGER IF EXISTS append_only_guard ON {table}"))
        conn.execute(
            text(
                f"CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION forbid_ledger_mutation()"
            )
        )


def enforce_append_only(engine) -> None:
    """Install a trigger that rejects any UPDATE or DELETE against an
    append-only table -- this must hold even for the table owner and even
    for a caller who forgot (or a future refactor that removed) the
    application-level `append_entry`/`append_correction` discipline in
    `app/services/ledger.py`. Idempotent: safe to call every time the
    schema is (re)created. See `enable_row_level_security`'s docstring
    for when to call `_apply_append_only(connection)` directly instead."""
    with engine.begin() as conn:
        _apply_append_only(conn)


def _apply_product_visibility_policy(conn) -> None:
    conn.execute(text("ALTER TABLE products ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE products FORCE ROW LEVEL SECURITY"))
    conn.execute(text("DROP POLICY IF EXISTS product_visibility ON products"))
    conn.execute(
        text(
            "CREATE POLICY product_visibility ON products "
            "USING (tenant_id = current_setting('app.tenant_id', true) "
            "OR lifecycle_state = 'PUBLISHED')"
        )
    )


def enable_product_visibility_policy(engine) -> None:
    """Bespoke RLS for `products`, deliberately NOT part of
    `_TENANT_SCOPED_TABLES`/`_apply_row_level_security`: that generic
    policy only ever lets a session see its own tenant's rows, but the
    public catalog (PU-02) must let an anonymous, no-tenant-scope session
    see every tenant's PUBLISHED products, while an admin session (AD-07)
    must still see its own tenant's rows in any lifecycle state. A single
    uniform per-table policy can't express both, so `products` gets its
    own policy: visible if it's this session's tenant, OR if it's
    published (visible to everyone, scoped or not). An unscoped session
    (no `app.tenant_id` set) can only ever match the second clause, so it
    sees published rows only -- never another tenant's drafts.

    Idempotent, same connection-vs-engine split as
    `enable_row_level_security` -- call `_apply_product_visibility_policy(
    connection)` directly from within an already-open transaction (e.g. an
    Alembic migration), this wrapper otherwise.
    """
    with engine.begin() as conn:
        _apply_product_visibility_policy(conn)


def _apply_content_document_visibility_policy(conn) -> None:
    conn.execute(text("ALTER TABLE content_documents ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE content_documents FORCE ROW LEVEL SECURITY"))
    conn.execute(text("DROP POLICY IF EXISTS tenant_isolation ON content_documents"))
    conn.execute(text("DROP POLICY IF EXISTS content_document_visibility ON content_documents"))
    conn.execute(
        text(
            "CREATE POLICY content_document_visibility ON content_documents "
            "USING (tenant_id = current_setting('app.tenant_id', true) "
            "OR state = 'PUBLISHED')"
        )
    )


def enable_content_document_visibility_policy(engine) -> None:
    """Bespoke RLS for `content_documents`, deliberately NOT part of
    `_TENANT_SCOPED_TABLES`/`_apply_row_level_security` -- same reasoning
    as `enable_product_visibility_policy`: PU-06 (the public methodology/
    risk/legal document page) must let an anonymous, no-tenant-scope
    session see every tenant's PUBLISHED documents, while AD-19's own
    admin session must still see its own tenant's rows in any state.
    An unscoped session (no `app.tenant_id` set) can only ever match the
    `state = 'PUBLISHED'` clause, so it sees published rows only -- never
    another tenant's draft or submitted-for-review content.

    Idempotent, same connection-vs-engine split as
    `enable_product_visibility_policy` -- call
    `_apply_content_document_visibility_policy(connection)` directly from
    within an already-open transaction (e.g. an Alembic migration), this
    wrapper otherwise.
    """
    with engine.begin() as conn:
        _apply_content_document_visibility_policy(conn)


def _apply_relay_role_access(conn) -> None:
    """The Signal Platform Integration Correction Pack's own
    INTEGRATION_DECISION.md S6/S8: the restricted relay worker's own
    ingress needs to look up `export_stream_registrations` BY
    `source_stream` to DISCOVER a tenant, before any `app.tenant_id`
    scope can be set -- the generic `tenant_isolation` policy (which
    only ever permits a session already scoped to its own tenant)
    cannot serve that lookup. `relay_role` is a distinct, restricted,
    non-superuser, non-BYPASSRLS login role (never `app_role`, never
    the browser-facing connection) that gets exactly one extra,
    permissive policy: unrestricted SELECT on `export_stream_
    registrations` alone. Postgres combines multiple PERMISSIVE
    policies on the same table with OR, so this does not weaken
    `tenant_isolation` for `app_role` or any other role -- it only
    grants `relay_role` a second way to satisfy this ONE table's
    SELECT. Every other tenant-scoped table `relay_role` touches
    (`inbox_events`, `ledger_entries`) is reached only through the
    ordinary `tenant_isolation` policy, exercised for real only after
    `set_tenant_scope` is called with the tenant the lookup found --
    resolving the gap `app/services/integration_inbox.py` explicitly
    deferred when this table was first added."""
    conn.execute(
        text(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'relay_role') THEN "
            "CREATE ROLE relay_role LOGIN NOSUPERUSER NOBYPASSRLS; "
            "END IF; END $$"
        )
    )
    conn.execute(text("GRANT SELECT ON export_stream_registrations TO relay_role"))
    #: UPDATE is needed too, not just SELECT/INSERT: `ingest_export_event`
    #: inserts the InboxEvent row first, then updates its own
    #: `applied_at`/`ledger_entry_id` columns once the ledger projection
    #: (if any) is applied -- never touching any OTHER row, but still a
    #: real UPDATE statement against this table.
    conn.execute(text("GRANT SELECT, INSERT, UPDATE ON inbox_events TO relay_role"))
    conn.execute(text("GRANT INSERT ON ledger_entries TO relay_role"))
    conn.execute(text("DROP POLICY IF EXISTS relay_stream_lookup ON export_stream_registrations"))
    conn.execute(
        text(
            "CREATE POLICY relay_stream_lookup ON export_stream_registrations "
            "AS PERMISSIVE FOR SELECT TO relay_role USING (true)"
        )
    )


def enable_relay_role_access(engine) -> None:
    """Idempotent, same connection-vs-engine split as
    `enable_row_level_security` -- call `_apply_relay_role_access(
    connection)` directly from within an already-open transaction (e.g.
    an Alembic migration), this wrapper otherwise."""
    with engine.begin() as conn:
        _apply_relay_role_access(conn)


def _apply_membership_self_lookup_policy(conn) -> None:
    """ADR-0009: `sign_in_submit`/`verify_email_page` (app/api/dashboard_
    routes.py) must look up the CALLER'S OWN `memberships` row by
    `user_id` to discover which tenant they belong to -- before any
    `app.tenant_id` scope can be set, since that scope is exactly what
    this lookup is trying to discover (the same bootstrap
    chicken-and-egg shape ADR-0002 already solved for `relay_role`'s own
    `export_stream_registrations` lookup, for a different role and a
    different table). The generic `tenant_isolation` policy on
    `memberships` (`_apply_row_level_security`) only ever permits a
    session already scoped to its own tenant, so under real `FORCE ROW
    LEVEL SECURITY` this lookup always returns zero rows once a real
    membership exists -- a genuine, previously-untested production
    login/verify-email outage (every existing test drove these routes
    through the Postgres superuser fixture, which bypasses RLS
    entirely and could never have caught this).

    Unlike `relay_role`'s bespoke policy, this one is not scoped `TO` a
    separate restricted role -- `app_role` is the ordinary,
    browser-facing login role, and this lookup happens on the exact
    same connection/session as everything else that role does. So the
    policy is instead scoped by VALUE: it only ever matches the one row
    whose `user_id` equals the session's own `app.current_user_id`
    setting (see `set_current_user_scope` below), a NEW session
    variable set only for the length of this one lookup -- never a
    caller-supplied tenant_id or user_id trusted for anything else.
    Postgres combines multiple PERMISSIVE policies on the same table
    with OR, so this does not weaken `tenant_isolation` for anyone --
    it only gives a session a second way to see ONE table's rows: its
    own membership rows, before any tenant scope exists. A session that
    never calls `set_current_user_scope` (i.e. every other request path
    in this codebase) leaves `app.current_user_id` unset, so this
    policy's `USING` clause never matches and grants nothing -- exactly
    like `tenant_isolation` itself fails closed when `app.tenant_id` is
    unset."""
    conn.execute(text("DROP POLICY IF EXISTS membership_self_lookup ON memberships"))
    conn.execute(
        text(
            "CREATE POLICY membership_self_lookup ON memberships "
            "AS PERMISSIVE FOR SELECT "
            "USING (user_id = current_setting('app.current_user_id', true))"
        )
    )


def enable_membership_self_lookup_policy(engine) -> None:
    """Idempotent, same connection-vs-engine split as
    `enable_row_level_security` -- call
    `_apply_membership_self_lookup_policy(connection)` directly from
    within an already-open transaction (e.g. an Alembic migration), this
    wrapper otherwise. Must run AFTER `enable_row_level_security` has
    already put `memberships` under `ENABLE`/`FORCE ROW LEVEL SECURITY`
    (this only adds an additional permissive policy to a table RLS is
    already active on -- it does not itself enable RLS)."""
    with engine.begin() as conn:
        _apply_membership_self_lookup_policy(conn)


def set_current_user_scope(session: Session, user_id: str) -> None:
    """Set the Postgres session variable `membership_self_lookup`
    (above) keys off of. Called ONLY around the one bootstrap lookup in
    `sign_in_submit`/`verify_email_page` that must find the caller's own
    membership row before `set_tenant_scope` can be called for real --
    never as a substitute for `set_tenant_scope`, and never trusted for
    anything beyond satisfying that one SELECT (same `set_config`-not-
    string-formatting reasoning as `set_tenant_scope`'s own docstring:
    Postgres's `SET` statement takes no bind parameters, so building it
    by hand would be an injection point)."""
    session.execute(text("SELECT set_config('app.current_user_id', :user_id, true)"), {"user_id": user_id})


def set_tenant_scope(session: Session, tenant_id: str) -> None:
    """Set the Postgres session variable the RLS policies above key off of.
    Must be called (with a real, authenticated tenant_id) at the start of
    every request/job that touches a tenant-scoped table -- never trust a
    caller-supplied tenant_id for anything other than this call itself,
    since a browser-supplied ID is not proof of access on its own (docs/02:
    "Never accept a browser tenant ID as proof of access"). Uses
    `set_config` (a function call, so it takes a bound parameter) rather
    than `SET LOCAL app.tenant_id = :tenant_id` -- Postgres's `SET`
    statement doesn't accept bind parameters at all, only string literals,
    which would make this an injection point if built by hand."""
    session.execute(text("SELECT set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": tenant_id})


@contextmanager
def session_scope(session_factory: sessionmaker) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
