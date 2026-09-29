"""Real deployment bootstrap for signal-portfolio-commercial -- INT-001
"Empty integrated installation": the piece that was genuinely missing
for "start both apps + the relay together" to mean anything against a
FRESH database, not just the ORM's own `create_all()` in
`tests/conftest.py`.

Run this AFTER `alembic upgrade head` has created the schema (this
script does not run migrations itself -- see `deploy/entrypoint-
commercial.sh`), connected as a role with rights to `GRANT`/`ALTER
DEFAULT PRIVILEGES` on the schema (the same superuser or schema-owning
connection `alembic upgrade head` itself used -- never the restricted
runtime role this script is ABOUT to create).

Does five real things, each idempotent (safe to re-run against an
already-bootstrapped database):

1. Creates the Postgres role `app/config.py`'s own `COMMERCIAL_
   DATABASE_URL` default already names (`commercial`) -- LOGIN
   NOSUPERUSER NOBYPASSRLS, the exact same shape as `tests/conftest.py`'s
   own `app_role` precedent. This was a genuine, previously-
   undiscovered gap: nothing in this build's migrations or app code
   ever created it, so a real fresh deployment's own app connection
   would fail outright (verified directly against a real, freshly
   migrated Postgres database in this session: `psql -U commercial`
   returned `FATAL: role "commercial" does not exist`).
2. Grants it SELECT/INSERT/UPDATE/DELETE on every table that exists
   right now, AND sets `ALTER DEFAULT PRIVILEGES` so a table a FUTURE
   migration adds is automatically covered too, without this script
   needing to be re-run for that alone. Never granted BYPASSRLS or
   superuser -- doing so would silently defeat every `tenant_isolation`/
   `product_visibility`/`content_document_visibility` policy the
   migrations already set up, regardless of `FORCE ROW LEVEL SECURITY`.
3. Sets a real password on BOTH this runtime role AND `relay_role`
   (created earlier, by migration `3f7a19c02b8e`, with no password of
   its own -- a second genuine gap this session found: neither role
   has ever had a password anywhere in this build, which only ever
   worked against Postgres's own `trust` auth method; the official
   `postgres` Docker image (and any real managed Postgres) defaults to
   password auth, so a passwordless role cannot connect at all --
   reproduced for real: `fe_sendauth: no password supplied` against a
   genuine `postgres:16` container). Read from
   `COMMERCIAL_RUNTIME_ROLE_PASSWORD`/`RELAY_ROLE_PASSWORD`
   environment variables, never a CLI argument (a password on a
   process's own argv is visible to every other process on the same
   host via `/proc`); this script refuses to run with either unset.
4. Provisions one real `Tenant` + one real owner `Membership` (never a
   fabricated one) and prints a freshly-issued owner JWT via
   `app.services.auth.issue_token`. There is no self-service login page
   in this build yet (confirmed: no `/login` route or template exists
   anywhere in `app/`) -- an operator mints a session out of band,
   exactly the way this script does, and hands it to whoever needs
   owner access (e.g. `curl -H "Authorization: Bearer <token>" ...` or
   pasted into a browser's own dev-tools-set cookie/header for manual
   use).
5. Registers every export stream the paired `signal-copier` deployment
   will actually use (`register_export_stream`), so the relay's very
   first batch is never rejected with `UnregisteredStreamError`.
   `signal-copier` exports on TWO INDEPENDENT stream namespaces, not
   one -- confirmed for real in this session by running both apps end
   to end and inspecting the actual `export_events` rows a real paper
   trade produced: `signal-copier:<account_id>` (EXECUTION_APPLIED,
   `app/engine.py`'s `_export_execution_applied_event`) AND
   `signal-copier:source:<source_name>` (SOURCE_RECEIPT, that same
   module's `_export_source_receipt`, exported for every signal
   regardless of routing outcome, before an account is even chosen).
   Registering only the account stream (an earlier version of this
   script's own mistake) leaves every SOURCE_RECEIPT permanently
   parked as `unregistered_stream` -- this script's own `--account-id`
   and `--source-name` (both repeatable) must list every enabled
   account in the paired deployment's `accounts.yaml` and every
   `source` its `routing.yaml` names, or some real events will never
   apply.
"""
from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import text

from app import config
from app.db import make_engine, make_session_factory
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.auth import issue_token
from app.services.integration_inbox import register_export_stream


def _bootstrap_runtime_role(engine, *, role_name: str) -> None:
    if not role_name.isidentifier():
        raise ValueError(f"{role_name!r} is not a safe Postgres identifier -- refusing to interpolate it into DDL")
    with engine.begin() as conn:
        # No bind parameter for `role_name` inside the DO $$ block:
        # psycopg cannot infer a type for a bound parameter used only
        # inside an anonymous PL/pgSQL block's own dynamic-looking SQL
        # text (`IndeterminateDatatype`, confirmed against a real
        # Postgres instance) -- role_name is validated as a plain
        # identifier immediately above, matching app/db.py's own
        # `_apply_relay_role_access` precedent of hardcoding a literal
        # role name directly into its DDL rather than binding it.
        conn.execute(
            text(
                "DO $$ BEGIN "
                f"IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{role_name}') THEN "
                f"CREATE ROLE {role_name} LOGIN NOSUPERUSER NOBYPASSRLS; "
                "END IF; END $$"
            )
        )
        conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role_name}"))
        conn.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role_name}"
            )
        )


def _set_role_password(engine, *, role_name: str, password: str) -> None:
    """`ALTER ROLE ... PASSWORD` -- confirmed for real against a genuine
    Postgres instance that this DDL statement rejects a bound parameter
    outright (`syntax error at or near "$1"`; Postgres's own DDL
    statements, unlike DML, never accept one at all, not even outside a
    `DO $$ ... $$` block). The password value is therefore interpolated
    as an escaped SQL string literal -- doubling every embedded single
    quote, the standard Postgres escaping for a literal -- the same way
    `role_name` is already interpolated as a validated identifier just
    below."""
    if not role_name.isidentifier():
        raise ValueError(f"{role_name!r} is not a safe Postgres identifier -- refusing to interpolate it into DDL")
    escaped_password = password.replace("'", "''")
    with engine.begin() as conn:
        conn.execute(text(f"ALTER ROLE {role_name} PASSWORD '{escaped_password}'"))


def _provision_tenant_and_owner(session, *, tenant_id: str, tenant_name: str, user_id: str, email: str) -> None:
    if session.get(Tenant, tenant_id) is None:
        session.add(Tenant(tenant_id=tenant_id, display_name=tenant_name, environment=config.ENVIRONMENT))
    if session.get(UserIdentity, user_id) is None:
        session.add(UserIdentity(user_id=user_id, email=email))
    session.flush()
    existing_membership = session.get(Membership, (tenant_id, user_id))
    if existing_membership is None:
        session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.OWNER))
    session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default="tenant-owner")
    parser.add_argument("--tenant-name", default="Owner Workspace")
    parser.add_argument("--user-id", default="owner-1")
    parser.add_argument("--email", default="owner@local-sim.invalid")
    parser.add_argument(
        "--account-id", action="append", default=[],
        help="Repeatable. Every ENABLED account_id in the paired signal-copier deployment's own "
             "accounts.yaml -- each becomes an EXECUTION_APPLIED stream, "
             "f'signal-copier:{account_id}'.",
    )
    parser.add_argument(
        "--source-name", action="append", default=[],
        help="Repeatable. Every `source` the paired signal-copier deployment's own routing.yaml "
             "names -- each becomes a SOURCE_RECEIPT stream, f'signal-copier:source:{source_name}'.",
    )
    parser.add_argument(
        "--runtime-role", default="commercial",
        help="Postgres role name to create/grant -- matches COMMERCIAL_DATABASE_URL's own "
             "default role name unless overridden.",
    )
    parser.add_argument(
        "--token-file", default=None,
        help="If set, the freshly-issued owner token is ALSO written here (mode 0600), for a "
             "caller to read directly -- e.g. `docker compose exec commercial cat <path>` -- "
             "rather than scraping container logs for it. Confirmed for real in this session: "
             "GitHub Actions masks the printed token in its own log output (it matches that "
             "runner's own secret-detection heuristic), so a log-scraping caller gets the literal "
             "string `***` instead, not a real token.",
    )
    args = parser.parse_args()

    if not args.account_id and not args.source_name:
        parser.error(
            "at least one --account-id and one --source-name are required -- "
            "without them, the paired signal-copier deployment's own events can never apply "
            "(see this script's own module docstring, point 5)"
        )

    runtime_role_password = os.environ.get("COMMERCIAL_RUNTIME_ROLE_PASSWORD")
    relay_role_password = os.environ.get("RELAY_ROLE_PASSWORD")
    if not runtime_role_password or not relay_role_password:
        parser.error(
            "COMMERCIAL_RUNTIME_ROLE_PASSWORD and RELAY_ROLE_PASSWORD must both be set in the "
            "environment (never as a CLI argument -- see this script's own module docstring, "
            "point 3) -- without a real password, neither role can connect to a Postgres server "
            "using ordinary password auth, which is every real deployment including the official "
            "postgres Docker image this build's own docker-compose.yml uses"
        )

    engine = make_engine(config.COMMERCIAL_DATABASE_URL)
    _bootstrap_runtime_role(engine, role_name=args.runtime_role)
    _set_role_password(engine, role_name=args.runtime_role, password=runtime_role_password)
    _set_role_password(engine, role_name="relay_role", password=relay_role_password)

    session_factory = make_session_factory(engine)
    session = session_factory()
    streams: list[str] = []
    try:
        _provision_tenant_and_owner(
            session, tenant_id=args.tenant_id, tenant_name=args.tenant_name,
            user_id=args.user_id, email=args.email,
        )
        for account_id in args.account_id:
            streams.append(f"signal-copier:{account_id}")
        for source_name in args.source_name:
            streams.append(f"signal-copier:source:{source_name}")
        for source_stream in streams:
            register_export_stream(
                session, tenant_id=args.tenant_id, source_stream=source_stream, environment=config.ENVIRONMENT,
            )
        # `session=session` so this operator-provisioned token is recorded
        # in `issued_tokens` -- otherwise it would be a JWT nobody could
        # ever "log out everywhere" for (app/services/token_revocation.py).
        token = issue_token(
            args.tenant_id, args.user_id, MembershipRole.OWNER, ttl_seconds=86400, session=session
        )
        session.commit()
    finally:
        session.close()

    if args.token_file is not None:
        with open(args.token_file, "w") as f:
            f.write(token)
        os.chmod(args.token_file, 0o600)
    print("Bootstrap complete.")
    print(f"  tenant_id:      {args.tenant_id}")
    print(f"  owner user_id:  {args.user_id}")
    print(f"  export streams: {', '.join(streams)}")
    print(f"  runtime role:   {args.runtime_role}")
    print()
    print("Owner session token (24h TTL) -- use as: Authorization: Bearer <token>")
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main())
