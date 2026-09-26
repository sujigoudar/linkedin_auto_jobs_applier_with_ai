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
