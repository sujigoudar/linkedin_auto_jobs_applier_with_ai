"""A real, disposable local PostgreSQL cluster for tests -- per
spec/docs/02_architecture_and_tenancy.md: "Local tests use disposable
PostgreSQL and a controlled JWT issuer." One cluster per test SESSION
(expensive to start), with tables dropped and recreated per test
function for isolation (cheap, and avoids one test's rows leaking into
another's assertions).

Requires the `postgresql-16` server binaries and a `psycopg` driver to
be installed (verified present/installable in this development
environment) -- if genuinely unavailable, tests using `postgres_engine`
skip rather than silently running against SQLite or a mock (this
package's own data types, e.g. RightsGrant.uses' ARRAY(String) column,
are Postgres-specific and would behave differently, or simply not work,
against anything else).
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import (
    Base,
    enable_product_visibility_policy,
    enable_row_level_security,
    enforce_append_only,
    make_engine,
    make_session_factory,
)

# Importing every model module (even ones this particular test file never
# references) is required so `Base.metadata` is fully populated before
# `create_all` -- otherwise running a single test file in isolation (e.g.
# `pytest tests/test_ledger.py`) would create only the tables THAT file's
# own imports happened to register, silently dropping tables other tests
# in the same session/run depend on existing.
import app.models.billing  # noqa: F401
import app.models.eligibility  # noqa: F401
import app.models.ledger  # noqa: F401
import app.models.portfolio_version  # noqa: F401
import app.models.product  # noqa: F401
import app.models.publication  # noqa: F401
import app.models.publisher_destination  # noqa: F401
import app.models.publisher_writer_claim  # noqa: F401
import app.models.release_review  # noqa: F401
import app.models.research_run  # noqa: F401
import app.models.rights  # noqa: F401
import app.models.sleeve  # noqa: F401
import app.models.support_case  # noqa: F401
import app.models.tenancy  # noqa: F401
import app.models.webhook_event  # noqa: F401

_PG_BIN = Path("/usr/lib/postgresql/16/bin")


def _pg_available() -> bool:
    return (_PG_BIN / "initdb").exists() and (_PG_BIN / "pg_ctl").exists()


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_as_postgres(cmd: list[str]) -> subprocess.CompletedProcess:
    """Postgres refuses to run its own server/init tools as root (most
    sandboxes, including this one, run tests as root) -- `su postgres -c`
    drops to the system `postgres` user for exactly those commands. A
    non-root test runner (e.g. a normal CI user) just runs them directly,
    since `su` would otherwise require a password it doesn't have."""
    if os.geteuid() == 0:
        quoted = " ".join(f"'{part}'" for part in cmd)
        return subprocess.run(["su", "postgres", "-c", quoted], capture_output=True, text=True)
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture(scope="session")
def postgres_cluster(tmp_path_factory):
    if not _pg_available():
        pytest.skip("postgresql-16 server binaries not found -- see this file's own docstring")

    data_dir = tmp_path_factory.mktemp("pgdata")
    # A world-writable-by-owner dir owned by `postgres` is required for
    # initdb/pg_ctl invoked via `su postgres` above to be able to write
    # into a directory pytest's tmp_path_factory created as root. pytest
    # also creates the ancestor dirs (basetemp, "pytest-of-root", ...) as
    # 0700 root:root, which blocks the `postgres` user from even traversing
    # down to data_dir regardless of data_dir's own ownership -- so every
    # ancestor up to (not including) the shared /tmp needs the execute bit
    # opened up for other users too.
    if os.geteuid() == 0:
        subprocess.run(["chown", "-R", "postgres:postgres", str(data_dir)], check=True)
        for ancestor in data_dir.parents:
            if ancestor == Path("/tmp") or ancestor == Path("/"):
                break
            os.chmod(ancestor, 0o711)

    port = _free_port()
    # --username=postgres pins the cluster's superuser role name
    # regardless of which OS user actually runs initdb -- without it,
    # initdb defaults the superuser's name to the invoking OS user's own
    # name (e.g. "runner" on a GitHub Actions runner, "postgres" only in
    # environments where a `postgres` system user happens to exist and
    # run this), and every connection string below hardcodes "postgres".
    init = _run_as_postgres(
        [str(_PG_BIN / "initdb"), "--auth=trust", "--username=postgres", "-D", str(data_dir)]
    )
    if init.returncode != 0:
        pytest.fail(f"initdb failed: {init.stdout}\n{init.stderr}")

    log_file = data_dir / "server.log"
    start = _run_as_postgres(
        [
            str(_PG_BIN / "pg_ctl"),
            "-D", str(data_dir),
            "-o", f"-p {port} -k {data_dir} -c listen_addresses=127.0.0.1",
            "-l", str(log_file),
            "-w",
            "start",
        ]
    )
    if start.returncode != 0:
        pytest.fail(f"pg_ctl start failed: {start.stdout}\n{start.stderr}\nlog:\n{_tail(log_file)}")

    try:
        _wait_ready(port)
        _run_as_postgres(
            [str(_PG_BIN / "createdb"), "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "commercial"]
        )
        # A plain, non-superuser, non-BYPASSRLS login role -- RLS tests must
        # run as this role, never as the `postgres` superuser used
        # everywhere else, since superusers bypass row-level security
        # regardless of FORCE ROW LEVEL SECURITY.
        _run_as_postgres(
            [
                str(_PG_BIN / "psql"),
                "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "-d", "commercial",
                "-c", "CREATE ROLE app_role LOGIN NOSUPERUSER NOBYPASSRLS",
            ]
        )
        yield {
            "admin_url": f"postgresql+psycopg://postgres@127.0.0.1:{port}/commercial",
            "app_role_url": f"postgresql+psycopg://app_role@127.0.0.1:{port}/commercial",
        }
    finally:
        _run_as_postgres([str(_PG_BIN / "pg_ctl"), "-D", str(data_dir), "-m", "fast", "stop"])
        shutil.rmtree(data_dir, ignore_errors=True)


def _wait_ready(port: int, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = _run_as_postgres([str(_PG_BIN / "pg_isready"), "-h", "127.0.0.1", "-p", str(port)])
        if result.returncode == 0:
            return
        time.sleep(0.2)
    pytest.fail(f"Postgres on port {port} never became ready")


def _tail(path: Path, lines: int = 40) -> str:
    if not path.exists():
        return "(no log file)"
    return "\n".join(path.read_text().splitlines()[-lines:])


@pytest.fixture
def db_session(postgres_cluster):
    engine = make_engine(postgres_cluster["admin_url"])
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_role"))
    enable_row_level_security(engine)
    enable_product_visibility_policy(engine)
    enforce_append_only(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def tenant_session_factory(postgres_cluster, db_session):
    """A session factory bound to the plain `app_role` login -- the role
    row-level-security tests actually exercise. Depends on `db_session` so
    the schema/grants/RLS policies it sets up exist first; the two
    fixtures' engines point at the same already-created database, `app_role`
    just sees it through RLS instead of as the unrestricted owner."""
    engine = make_engine(postgres_cluster["app_role_url"])
    try:
        yield make_session_factory(engine)
    finally:
        engine.dispose()
