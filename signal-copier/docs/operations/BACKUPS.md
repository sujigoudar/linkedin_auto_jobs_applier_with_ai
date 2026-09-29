# Backups

## What's real: Litestream WAL replication (design draft, not yet operated)

`deploy/litestream/litestream.yml` configures Litestream to replicate the
active site's SQLite WAL (`/var/lib/signal-copier/signal_copier.db`) to one
private Cloudflare R2 bucket, continuously:

```yaml
dbs:
  - path: /var/lib/signal-copier/signal_copier.db
    replicas:
      - type: s3
        endpoint: ${LITESTREAM_R2_ENDPOINT}
        bucket: signal-copier-backup
        path: primary
        region: auto
        sync-interval: 10s
        retention: 72h
```

Credentials (`LITESTREAM_ACCESS_KEY_ID` / `LITESTREAM_SECRET_ACCESS_KEY`)
come from environment variables, never committed to this file.

### Restore procedure (`deploy/RUNBOOK.md` step 3)

```bash
litestream restore -o /var/lib/signal-copier/restored.db \
  -config /etc/signal-copier/litestream.yml \
  /var/lib/signal-copier/signal_copier.db
```

Always restore to a **new path**, never overwrite anything, then verify
before pointing the app at it: `sqlite3 restored.db "PRAGMA
integrity_check;"`, and check every `lifecycle_state` row's
`pending_entry`/`pending_exit` — those carry the only record of an
in-flight order for a not-yet-protected symbol.

## What this is — and honestly isn't

- **Asynchronous DR, a measured RPO, never zero.** `deploy/litestream/litestream.yml`'s
  own comment states this plainly: this is not active-active SQLite. A
  promoted site's restored database may be missing the most recent writes
  from before an incident — `docs/FAILOVER.md` states this codebase "has
  no visibility into replication freshness at all."
- **Client-side encryption is NOT provided.** Litestream v0.5+ removed the
  old built-in Age encryption. R2's provider-side encryption-at-rest is
  not the same as end-to-end/client encryption — do not claim it is.
- **Cost/quota bounded, not unlimited.** A naive very-short sync interval,
  sustained for a month, can exceed R2's free Class A operation allowance
  on its own, before snapshot/multipart/list calls are counted. The
  configured `sync-interval: 10s` / `retention: 72h` are starting points —
  measure actual PUT volume via R2's own usage dashboard before tightening
  the interval.
- **Not yet operated against a real deployment.** Per `deploy/README.md`'s
  own "Applied?" column, `litestream/litestream.yml` needs real R2
  credentials and a tested restore before it backs anything real. This
  session's own work static-validated the file (valid YAML, correct
  `DATABASE_PATH` cross-reference against `app/config.py`) but never ran
  it against a live bucket.
- **Restore path is manual, not one-command.** There is no automated
  "restore and verify" tool in this codebase — the RUNBOOK's step 3 is a
  human running `litestream restore` and manually inspecting the result.

## What is honestly NOT covered

- **No backup of anything other than the SQLite database file.**
  `config/routing.yaml`, `config/accounts.yaml`, and `.env`/environment
  variable state are not backed up by Litestream — `deploy/RUNBOOK.md`
  step 3 explicitly calls out that these files "must also be present and
  current" for a restore to be complete, but nothing in this codebase
  automates their backup or verifies they exist alongside a database
  restore. Losing the host that holds these files, without a separate
  backup of them, is a real, undocumented-elsewhere gap this document is
  flagging honestly: there is no backup system for config/secrets today,
  only for the database.
- **No point-in-time recovery beyond Litestream's own snapshot/WAL
  retention (72h in the current template).** An incident discovered more
  than 72 hours after it happened has no guaranteed recovery point.
- **No tested backup-integrity monitoring.** Nothing alerts if Litestream
  itself silently stops replicating (a stopped Litestream process would
  leave the R2 bucket stale with no application-level signal — the
  Cloudflare Worker heartbeat in `deploy/cloudflare-heartbeat` monitors
  Site A's `/health`, not Litestream's own replication health).

This is a real, designed mechanism with a genuine gap between "designed"
and "operated" — treat it as unverified until a real restore has been
exercised against a live deployment, and treat config/secret backup as an
unaddressed gap, not an oversight to be quietly assumed covered by the
database backup.
