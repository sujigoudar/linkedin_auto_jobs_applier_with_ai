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
    "memberships", "customer_profiles", "ledger_entries", "sleeves", "subscriptions",
)

#: Tables that must never be UPDATEd or DELETEd from, only appended to (see
#: app/models/ledger.py) -- a mistaken entry is corrected by inserting a new
#: row, never by editing or removing the original.
_APPEND_ONLY_TABLES: tuple[str, ...] = ("ledger_entries",)


def enable_row_level_security(engine) -> None:
    """Enable and FORCE row-level security on every tenant-scoped table, with
    a policy that only ever permits rows matching the session's
    `app.tenant_id` setting -- and permits none at all when that setting is
    unset (fail-closed: no scope set means no visibility, not "everything").

    Idempotent: safe to call every time the schema is (re)created."""
    with engine.begin() as conn:
        for table in _TENANT_SCOPED_TABLES:
            conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            conn.execute(text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}"))
            conn.execute(
                text(
                    f"CREATE POLICY tenant_isolation ON {table} "
                    "USING (tenant_id = current_setting('app.tenant_id', true))"
                )
            )


def enforce_append_only(engine) -> None:
    """Install a trigger that rejects any UPDATE or DELETE against an
    append-only table -- this must hold even for the table owner and even
    for a caller who forgot (or a future refactor that removed) the
    application-level `append_entry`/`append_correction` discipline in
    `app/services/ledger.py`. Idempotent: safe to call every time the
    schema is (re)created."""
    with engine.begin() as conn:
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
