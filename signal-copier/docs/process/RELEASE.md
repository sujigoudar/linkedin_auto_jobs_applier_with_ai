# Release process

Honest statement: **there is no formal release process for this project
yet.** This is pre-1.0, development-branch software. There are no
version tags on `signal-copier` in this repository, no `CHANGES`/release
notes published as GitHub Releases, no semantic-versioning scheme in
`pyproject.toml` beyond whatever the package metadata defaults to, and
no promotion pipeline from a "release candidate" branch to a "stable"
branch. Work lands directly on the shared integration branch
(`claude/signal-copier-redesign`, see `docs/process/GIT.md`) and that
branch's tip is the only thing that currently exists as "the current
state of the project." Do not describe a release train, a changelog
release cadence, or a tagged-version support policy as existing here —
none of it does yet, and inventing one in documentation would be
misleading about what this codebase actually guarantees.

`app/main.py`'s own `/system/readiness` endpoint reflects this same
honesty: `release_status` is an explicit, documented placeholder
(`"No qualification/release-approval taxonomy exists yet in this
build."`) rather than a fabricated approved/rejected state — see
`docs/state/PENDING_DECISIONS.md`.

## What does exist: a real Docker-based deployment artifact

There is a genuine, working containerized deployment story, and it's the
closest thing to a "release artifact" this project has:

- **`Dockerfile`** builds a real production image. It must be built with
  the monorepo root as build context (`docker-compose.yml`'s
  `context: ..`, `dockerfile: signal-copier/Dockerfile`) because the
  image depends on the sibling `signal_platform_contracts` package — the
  Dockerfile's own comments record that this was confirmed by an actual
  failed build attempt in-session, not assumed. The image drops to a
  non-root user, and base requirements deliberately exclude
  optional/heavy broker SDKs (ccxt, discord.py, metaapi-cloud-sdk, …)
  unless built with `--build-arg EXTRAS="..."`, so a deployment only
  needs to trust the SDKs it actually uses.
- **`docker-compose.yml`** is the real deployment shape: loopback-only
  port binding by default (an HTTPS-terminating reverse proxy is expected
  in front for a real deployment), a read-only root filesystem with
  `cap_drop: ALL` and `no-new-privileges` (DEP-01), a named volume for
  the SQLite database (chosen over a bind-mounted single file for more
  reliable cross-platform locking), and resource limits
  (`mem_limit: 512m`, `cpus: 1.0`).
- **`.github/workflows/integration-docker-build-ci.yml`** exercises the
  real container build in CI, so a broken build context or a missing
  runtime dependency is caught before anyone tries to deploy from a
  stale image.
- **`deploy/`** holds the real operational surface around that image:
  `deploy/RUNBOOK.md` (the actual step-by-step promotion procedure),
  plus `systemd/`, `terraform/`, `cloud-init/`, `litestream/` (SQLite
  replication), and `cloudflare-heartbeat/` — real infrastructure-as-code
  and operational scaffolding, not placeholders.

## What "shipping a change" actually means today

A change is released, in the only sense that currently applies, when it
is pushed to `claude/signal-copier-redesign` and passes
`.github/workflows/signal-copier-ci.yml` and
`.github/workflows/integration-docker-build-ci.yml`. There is no
additional gate beyond that. An operator deploying this software today
would build the Docker image from a specific commit on that branch and
run `deploy/RUNBOOK.md`'s procedure — there is nothing more formal to
point at, and this document should be updated (not left stale) the day
that changes.
