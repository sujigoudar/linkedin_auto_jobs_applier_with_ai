# Release

Honest framing first: **this is pre-1.0, development-branch software.**
There is no tagged release, no published package, and no live
production deployment. `ops/COMMERCIAL_RESUME.md` states this
directly: "This build has NOT reached a state ready for any live/real
activity." Everything described below runs in `LOCAL_SIM`/paper mode.
Six owner-only "action cards" (legal entity, source rights contracts,
platform agreements, payment processor/merchant approval, customer
agreements, and the final go-live decision — see
`docs/PENDING_DECISIONS.md`) remain outstanding and are explicitly
**not** things more coding can resolve.

## What actually exists: a Docker image and a compose stack

There is no CI/CD pipeline that builds and pushes a versioned image,
and no deployment target beyond a developer's own machine. What exists
is a real, runnable local artifact:

- **`signal-portfolio-commercial/Dockerfile`** — builds a
  `python:3.11-slim` image, installs the editable
  `signal_platform_contracts` sibling package first (so
  `requirements.txt`'s own `pip install` can resolve it), copies the
  app in, and runs as a dedicated non-root `commercial` user (no
  superuser inside the container, matching `signal-copier`'s own
  Dockerfile precedent).
- **`docker-compose.yml`** (repo root) — brings up both real apps
  (`signal-copier` + `signal-portfolio-commercial`) plus a real
  `postgres:16` service, wired together with the actual in-process
  relay (`signal-copier/app/relay_worker.py` +
  `app/relay_scheduler.py`, started from that app's own `lifespan`
  handler — confirmed by reading that code, not assumed; there is no
  separate third "relay" container). Everything runs against synthetic
  secrets in `LOCAL_SIM`/paper mode — no live broker, vendor, or
  payment credential is required to bring the stack up.

```
docker compose up --build
curl http://localhost:8001/healthz
```

The commercial container's own startup logs print a one-time owner
session token minted by `ops/bootstrap.py` (grep the compose file's
own usage comment for the exact `docker compose logs`/`docker compose
exec` incantations — a CI runner that masks secret-looking log output
may hide the printed token, in which case read
`/tmp/owner_token.txt` inside the container directly).

## `deploy/entrypoint-commercial.sh` — the real release/startup ordering

This is the actual "release process" this app has: a container
entrypoint that runs, in order, before the server ever accepts a
request:

1. `alembic upgrade head`, against `COMMERCIAL_MIGRATOR_DATABASE_URL`
   — a **separate, more-privileged** connection string than the
   runtime `COMMERCIAL_DATABASE_URL`. The restricted runtime role
   (`commercial`, itself created by this same startup sequence) must
   never hold the DDL/role-management rights the migrations and
   bootstrap step need.
2. `ops/bootstrap.py`, run under the same migrator connection — real,
   idempotent provisioning: creates the restricted Postgres runtime
   role and sets its password via `ALTER ROLE` (a passwordless role
   only works against Postgres's `trust` auth method, which the
   official `postgres:16` image does not use by default — this was
   hit for real during development, `fe_sendauth: no password
   supplied`), provisions a tenant/owner, registers the export stream.
3. Only then does it `exec` the real command (`uvicorn app.main:app
   --host 0.0.0.0 --port 8001`).

This ordering is deliberate, not incidental — see the script's own
comment: never collapse these three steps into one opaque `CMD`, so a
migration or bootstrap failure fails the container's own startup
loudly instead of leaving `uvicorn` running against a half-migrated
schema.

`docker-compose.yml`'s own comment notes that using the Postgres
superuser as `COMMERCIAL_MIGRATOR_DATABASE_URL` is fine for this
local/paper stack, but a real production deployment should use a
dedicated migrator role (`CREATEROLE` + ownership of the target
schema) instead of the database's own superuser — this has not been
built, since no production deployment exists yet.

## Versioning

There is no semantic-versioning scheme or git tag convention in active
use for this app. The closest thing to a version marker is
`ops/commercial_state.json`'s own `package_version` field
(`"Signal_Portfolio_Commercial_Platform_Claude_Code_Pack_v1"`), which
tracks the spec package this build was built against, not a release of
this app itself. Do not invent tags or a version number that implies a
release process that does not exist.

## Before anything here is a real release

Per `ops/COMMERCIAL_RESUME.md`'s own standing directive from the user:
"BEFORE anything reaches a point of actually going live (a real Stripe
charge, a real Collective2/eToro publish, a real broker-managed
account), stop and notify the user explicitly rather than proceeding."
This is not a suggestion to route around — see `docs/PENDING_DECISIONS.md`
for the six standing action cards this gates on.
