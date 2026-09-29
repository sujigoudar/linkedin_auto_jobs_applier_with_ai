# Deployment

## Single-site: Docker / docker-compose

The primary, tested deployment path is a single Docker container.

```bash
cp .env.example .env
cp config/routing.example.yaml config/routing.yaml
cp config/accounts.example.yaml config/accounts.yaml

docker compose up --build
```

- Build context is the **repo root**, not `signal-copier/` alone — the
  `signal_platform_contracts` sibling package is pulled in as an editable
  dependency (`-e ../signal_platform_contracts` in `requirements.txt`), so
  `docker-compose.yml`'s `signal-copier` service sets `context: .`,
  `dockerfile: signal-copier/Dockerfile`.
- The image (`python:3.11-slim`) runs as a **non-root** user
  (`signal-copier`, no-login shell) — `/app` and `/app/data` are
  chown'd to it at build time.
- The database lives in a named Docker volume (`signal_copier_db`) so it
  survives container recreation; `config/` is bind-mounted so
  routing/account changes don't require a rebuild.
- Optional broker-adapter dependencies (ccxt, discord.py, metaapi-cloud-sdk,
  etc.) are **not** installed by default — bake one in with
  `docker build --build-arg EXTRAS="ccxt tweepy" .` or the equivalent
  `build.args.EXTRAS` line in `docker-compose.yml`.
- `docker-compose.yml`'s port publish is **loopback-only**
  (`127.0.0.1:8000:8000`) by design — publishing on every host interface
  would directly expose this app's plain HTTP (no TLS) to the network. Put
  an HTTPS-terminating reverse proxy (nginx/Caddy) in front for anything
  beyond local use, with the container reachable only from that proxy.
- The base image tag is a floating minor version, not pinned to a digest —
  pin it as part of an actual release process once there's a real
  multi-arch build to verify against.
- Verified in this codebase's own CI/sandbox work: the image builds and
  runs, `/health` responds, and a webhook signal correctly routes through
  to the paper broker and updates `/positions` inside the running
  container.

## Required environment variables at startup

At minimum, for any real (non-local-only) deployment:

- `OWNER_PASSWORD` **or** `OWNER_PASSWORD_HASH` (never both), plus
  `SESSION_SECRET` — otherwise every owner-gated endpoint fails closed
  with `503`. See `docs/security/SECRETS.md`.
- `WEBHOOK_SHARED_SECRET` before exposing `/webhook/*` — otherwise that
  route stays disabled (`503`).
- Broker credentials for every destination account configured in
  `config/accounts.yaml` (per-adapter env vars — see
  `docs/security/SECRETS.md`).
- `WRITER_SITE_ID` for any deployment where more than one host could ever
  plausibly run this app (see `docs/operations/ENVIRONMENTS.md` and
  `docs/FAILOVER.md`) — falls back to `socket.gethostname()` if unset,
  which is unsafe when two hosts could share a hostname (containers,
  generic cloud images).

Nothing else is strictly required to start; every other setting in
`app/config.py` has a safe default (see `docs/operations/CONFIGURATION.md`
for the full reference).

## Multi-site: guarded active/passive (design draft, not yet applied)

`deploy/` holds a reviewed design and Infrastructure-as-Code draft for
running one active site plus one warm standby. **None of it has been
applied against a real cloud account** — it is reviewable
Terraform/cloud-init/systemd/Worker source, static-validated (see
`deploy/README.md`'s "Static validation performed" section: `terraform
fmt`, `systemd-analyze verify`, YAML/TOML parsing, and cross-checks against
this codebase's real `/health` shape and config), not an executed
deployment.

### Topology

- **Site A (active)**: the operator's existing VPS, if it has spare
  capacity and reaches every configured broker/source. Runs the trading
  service (`deploy/systemd/signal-copier.service`).
- **Site B (standby)**: a separate host (the draft targets Oracle Cloud
  Always Free A1, 2 OCPU/12 GB), provisioned but never started as a
  trading writer. Runs only a read-only status unit
  (`deploy/systemd/signal-copier-standby-status.service`) and a Litestream
  restore target — `STANDBY_MODE=true` on this host, which the
  `_standby_read_only_gate` middleware (`app/main.py`) enforces: every
  non-GET request gets `503`, and signal ingestion / background
  reconciliation / price polling never start.
- **Cloudflare (independent monitor + off-host backup only)**: a Worker
  (`deploy/cloudflare-heartbeat`) polls Site A's `/health` on a schedule
  and alerts on sustained failure — it never holds a broker credential and
  never starts a trading process. Litestream replicates the SQLite
  database's WAL to a private R2 bucket (`deploy/litestream/litestream.yml`).

This is **exactly one active writer per brokerage account at all times** —
not redundant execution, not zero-RPO, and not automatic failover. See
`docs/FAILOVER.md` for the fencing mechanism and `deploy/RUNBOOK.md` for
the human-executed promotion procedure; a different site only ever becomes
the writer through `python -m app.promote_cli promote`, never
automatically.

### What multi-site deployment deliberately does not do

- Provision anything against a real account by itself — `terraform apply`
  and `wrangler deploy` have not been run against real cloud credentials
  in producing this draft.
- Enable automatic site promotion under any configuration.
- Put broker credentials anywhere but the active site's own environment
  variables (never in Terraform state, never in cloud-init, never in the
  Cloudflare Worker).
- Run two active writers against the same brokerage account — the
  standby's trading service unit exists on disk but is never enabled until
  a human completes the promotion runbook.
