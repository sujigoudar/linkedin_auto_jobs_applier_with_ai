# Deployment: guarded active/passive, not multi-cloud active-active

This directory is a **design and IaC draft**, not an executed deployment.
Nothing here has been applied against real cloud accounts — this session has
no Hostinger/OCI/GCP/Cloudflare credentials or account access, so every file
here needs a human (or a session with real cloud access) to review, adapt the
placeholder values, and actually run it.

## Topology

- **Site A (active): your existing Hostinger VPS**, if a manual check confirms
  it has spare CPU/RAM/disk, isn't already saturated by other workloads on
  that box, and its network path reaches every broker/source this deployment
  needs. This is a paid resource you already have — nothing to provision.
- **Site B (standby): Oracle Cloud Always Free A1**, provisioned but never
  started as a trading writer. It runs only a read-only status page and a
  Litestream restore target until a human executes the promotion runbook.
  Current Oracle Always Free A1 allowance is **2 OCPUs / 12 GB RAM
  equivalent** (not the older 4/24 figure) — `deploy/terraform/oci-standby`
  requests exactly that, once.
- **Cloudflare: independent monitor + off-host backup only.** A Worker
  (`deploy/cloudflare-heartbeat`) polls Site A's `/health` on a schedule and
  alerts (webhook/email) if it stops responding — it never holds a broker
  credential and never starts a trading process. R2 is Litestream's
  replication target for the SQLite database (`deploy/litestream`).

This is **exactly one active writer per brokerage account** at all times. It
is not redundant execution, not zero-RPO, and not automatic failover — see
`RUNBOOK.md` for why automatic promotion stays disabled by default and what
has to be true before a human enables it.

## What's here

| Path | Purpose | Applied? |
|---|---|---|
| `terraform/oci-standby/` | OCI A1 standby instance (stopped-by-default trading service, tagged, Always Free shape) | No — needs your OCI tenancy/compartment OCIDs filled into `terraform.tfvars` |
| `cloud-init/standby-init.yaml` | First-boot script for the standby: installs the app, enables the read-only status unit, does **not** enable the trading service, does **not** write broker credentials | No |
| `systemd/signal-copier.service` | The trading service unit (enabled on Site A only) | No |
| `systemd/signal-copier-standby-status.service` | Read-only status/health page unit (enabled on Site B) | No |
| `cloudflare-heartbeat/` | Wrangler-based Worker: polls Site A `/health`, alerts on sustained failure, never touches a broker | No — needs `wrangler login` and a webhook/email target |
| `litestream/litestream.yml` | Litestream config template: replicate `signal_copier.db`'s WAL to one private R2 bucket | No — needs R2 credentials and a tested restore first |
| `RUNBOOK.md` | The actual promotion/fencing/reconciliation procedure a human runs before ever making the standby a writer | Documentation, not automation |

## Static validation performed (no cloud credentials, still real checks)

This sandbox has no OCI/Cloudflare/R2 credentials, so nothing here could be
`terraform apply`'d, `wrangler deploy`'d, or actually provisioned -- but the
following checks ran for real and their results are accurate as of the
commit that added this section:

- `terraform fmt` on `terraform/oci-standby/main.tf` (found and fixed real
  alignment drift; `terraform validate`/`init` itself could not run --
  `registry.terraform.io` is unreachable from this sandbox).
- `systemd-analyze verify` on both `.service` units — both parse as valid
  unit files; the only reported issue is the trading binary not existing
  on this sandbox (expected, since it isn't a real deployed host).
- `cloud-init/standby-init.yaml`, `litestream/litestream.yml`, and
  `cloudflare-heartbeat/wrangler.toml.example` all parse as valid
  YAML/YAML/TOML respectively.
- `cloudflare-heartbeat/worker.js` passes `node --check` (valid JS syntax).
- Cross-checked `worker.js`'s expected `/health` JSON shape
  (`status`/`database_ok`/`price_monitor_ok`/`reconciler_ok`) against
  `app/main.py`'s actual `GET /health` handler — fields match exactly.
- Cross-checked `STANDBY_MODE`/`_standby_read_only_gate` and
  `DATABASE_PATH` references in this directory against `app/config.py`/
  `app/main.py` — all real, all match.
- Fixed two dangling references to design documents (`docs/
  02_CLOUD_SELECTION.md`, `docs/03_REDUNDANCY_AND_DATA.md`) that were never
  actually part of this repository — replaced with inline content or an
  honest note that the criteria came from an external review, not a file
  here.

None of this substitutes for `terraform validate`/`plan` or an actual test
deployment once real cloud credentials are available — see each row's
"Applied?" column above.

## What this deliberately does NOT do

- Provision anything against a real account (no `terraform apply`, no
  `wrangler deploy`, no SSH to a real host was run in producing these files).
- Enable automatic site promotion. A human executes `RUNBOOK.md` end to end;
  nothing here auto-promotes the standby on a missed heartbeat.
- Put broker credentials anywhere but the active site's own environment
  variables (never in Terraform state, never in the cloud-init file, never in
  the Cloudflare Worker).
- Run two active writers against the same brokerage account. The standby's
  trading service unit is present on disk but never enabled until a human
  completes the promotion runbook.
