# Backups

## What is stated as a requirement

`spec/docs/13_operations_deployment_and_cost.md`'s operations/deployment
section states the requirement in one sentence: "Publication identity and
control-plane database are backed up with legal retention and credential
separation." It also lists "backup generation age" among the monitoring
signals a real deployment must track, and "backup restoration" among the
items a customer-ready launch needs a documented, owned procedure for.

## What actually exists in this codebase

**Nothing.** There is no backup-taking script, cron job, `pg_dump`
wrapper, WAL-archiving configuration, storage-lifecycle policy, or backup-
verification code anywhere in this repository. This was checked directly:
neither `deploy/`, `ops/`, nor the Dockerfile reference any backup
tooling, and no `docker-compose.yml`-level volume/snapshot configuration
for the Postgres data directory was found in this service's own files.

This is a genuine, disclosed gap, not an oversight to paper over:

- **No automated backup schedule.** Whatever backup cadence a real
  deployment runs is entirely a property of the hosting platform (e.g. a
  managed Postgres provider's own snapshot feature), configured outside
  this codebase.
- **No backup-retention policy encoded anywhere.** "Legal retention" is
  named as a requirement in the spec but has no corresponding
  implementation, configuration, or even a stated retention period in this
  repository.
- **No credential-separation mechanism for backup access specifically.**
  General credential separation exists for the *runtime* roles
  (`docs/security/SECRETS.md`, `docs/operations/DEPLOYMENT.md`'s
  migrator-vs-runtime-role split), but nothing in this codebase
  provisions a distinct, narrower-scoped role for whatever process
  actually reads/writes backup artifacts.
- **No backup-restore drill or tooling.** `docs/operations/DR.md`
  documents at length why a restore, once performed, is *safe to replay
  against* (append-only ledger, idempotent ingest) -- but performing the
  restore itself (taking a snapshot, validating it, restoring it into a
  new instance) has no code or script in this repository.

## What this means practically

Anyone deploying this service for real needs to independently establish:

1. A real backup mechanism for the Postgres database backing
   `COMMERCIAL_DATABASE_URL` (managed-provider snapshots, `pg_dump` on a
   schedule, WAL archiving for point-in-time recovery, or equivalent).
2. A stated retention period that satisfies whatever legal/regulatory
   retention this deployment's jurisdiction requires for financial and
   audit records (see `docs/security/THREAT_MODEL.md` asset #2 and #6).
3. A distinct, minimally-privileged credential for whatever backup agent
   performs the snapshot/restore, separate from the `commercial` runtime
   role and the `COMMERCIAL_MIGRATOR_DATABASE_URL` migrator credential.
4. A documented, owned restore procedure and an owner for it -- per
   `spec/docs/13_operations_deployment_and_cost.md`'s "A customer-ready
   launch also needs ... backup restoration ... and business-continuity
   owner."

None of the above is a claim that this codebase's data model is unsafe to
back up -- the append-only ledger and idempotent ingest documented in
`docs/operations/DR.md` mean a real backup/restore mechanism, once built,
would have real correctness guarantees to lean on. The gap is specifically
that the backup mechanism itself has not been built yet.
