#!/bin/sh
# Real deployment entrypoint: runs the real migration chain, THEN the
# real idempotent bootstrap (Postgres runtime role + tenant/owner +
# export stream registration -- ops/bootstrap.py's own docstring has
# the full "why", including the genuine gaps this session found and
# fixed by verifying an actual `alembic upgrade head` against a real,
# freshly created Postgres database for the first time in this build's
# history), BEFORE starting the real ASGI server -- never all three as
# one opaque `CMD`, so a migration or bootstrap failure fails the
# container's own startup loudly instead of leaving `uvicorn` running
# against a half-migrated schema.
#
# `COMMERCIAL_MIGRATOR_DATABASE_URL` is a SEPARATE, more-privileged
# connection string than `COMMERCIAL_DATABASE_URL` -- alembic and the
# bootstrap script's own role/GRANT DDL need rights the restricted
# runtime role (`commercial`, created BY this very step) must never
# have itself. docker-compose.yml's own `commercial` service sets it to
# the Postgres superuser connection for local/paper use; a real
# production deployment should instead use a dedicated migrator role
# with CREATEROLE + ownership of the target schema, never the
# database's own superuser.
set -eu

echo "[entrypoint] running alembic upgrade head..."
COMMERCIAL_DATABASE_URL="${COMMERCIAL_MIGRATOR_DATABASE_URL:?COMMERCIAL_MIGRATOR_DATABASE_URL must be set}" \
  python3 -m alembic upgrade head

echo "[entrypoint] running ops/bootstrap.py..."
# shellcheck disable=SC2086
COMMERCIAL_DATABASE_URL="${COMMERCIAL_MIGRATOR_DATABASE_URL}" \
  python3 ops/bootstrap.py ${BOOTSTRAP_ARGS:-}

echo "[entrypoint] starting: $*"
exec "$@"
