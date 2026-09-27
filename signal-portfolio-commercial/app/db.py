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
    "release_reviews",
)

#: Tables that must never be UPDATEd or DELETEd from, only appended to (see
#: app/models/ledger.py) -- a mistaken entry is corrected by inserting a new
#: row, never by editing or removing the original. portfolio_versions/
#: portfolio_version_sleeves are append-only for the same reason
#: (app/models/portfolio_version.py): "Historical membership is never
#: overwritten" -- a weight change is a new version's rows, never an edit.
_APPEND_ONLY_TABLES: tuple[str, ...] = ("ledger_entries", "portfolio_versions", "portfolio_version_sleeves")


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


def _apply_append_only(conn) -> None:
    conn.execute(
        text(
            "CREATE OR REPLACE FUNCTION forbid_ledger_mutation() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'this table is append-only: % is not permitted', TG_OP; "
            "END; $$ LANGUAGE plpgsql"
        )
    )
    for table in _APPEND_ONLY_TABLES:
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
