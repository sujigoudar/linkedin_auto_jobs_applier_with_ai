# Non-Functional Requirements

Real, evidenced constraints this codebase actually enforces — each
backed by a specific mechanism, not an aspirational SLA.

## Fail-closed risk and execution semantics

Every ambiguous or unresolved outcome in a money-moving path is
treated as **not safe**, never defaulted to a permissive assumption:

- **Capital admission** (`app/capital_allocator.py`, ADR-0007): a
  signal with no resolvable price is rejected, not skipped, whenever
  any gate is configured. A position whose exposure this service
  cannot resolve blocks new admissions for that account rather than
  contributing `0.0` to the exposure total. Risk-basis sizing rejects
  whenever `stop_loss` or a real, fresh `equity` figure is missing.
- **Command outcomes** (`app/command_ledger.py`, ADR-0004): a broker
  call that raises, times out, or returns PENDING with no
  `broker_order_id` is recorded as `UNKNOWN_AMBIGUOUS` — never guessed
  as success or failure, and resolvable only by independent
  reconciliation.
- **Writer fencing** (`app/writer_lease.py`, ADR-0002): no acquired
  token, a lease row that's gone, or any DB error reading the lease
  all raise `FencedOutError` — never treated as "still valid."
- **Plain-account close reconciliation** (`app/engine.py`'s
  `_reconcile_before_plain_close`): a close proceeds only with a
  fresh, matching broker position readback, or an explicit,
  off-by-default `exclusive_writer_qualified` assertion — never
  against a possibly-stale local projection by default.
- **Qualification** (`app/qualification.py`, ADR-0006): a route cannot
  be recorded at a ladder state whose prerequisites are unmet, and
  states requiring feedback cannot be recorded for an adapter that
  structurally cannot verify it.

## Single-writer enforcement

Exactly one process may execute broker-write commands at a time,
enforced by two independent, non-substitutable layers (ADR-0002,
ADR-0003, `docs/design/WRITER_FENCING.md`):

1. A human-executed, checklist-gated promotion procedure
   (`deploy/RUNBOOK.md`) is the actual proof a prior writer's host
   cannot reach the broker.
2. A monotonic fencing token (`writer_lease`), checked before every
   broker-write call, immediately fences out any process whose token
   has been superseded — independent of whether its own lease row
   would otherwise still look unexpired.

No automatic failover exists anywhere in the codebase; a standby
process is refused write access structurally
(`_standby_read_only_gate`), never merely by convention.

## Durability of in-flight financial state

State a crash could otherwise silently lose is written durably and
recoverable on restart, evidenced directly by three tables:

- **`command_ledger`** (ADR-0004): every real broker command is
  recorded, committed, **before** the broker call is made — a crash
  between that commit and the broker call leaves a real
  `pending_submission` row for a restart to find, not silence.
- **`capital_reservations`** (P0-4, ADR-0007): a capital reservation is
  written durably the instant admission succeeds, before the broker
  call it's gating starts. `CapitalAllocator.__init__` reloads every
  unresolved row on restart, so a crash does not silently forget a
  reservation the broker may have already accepted.
- **`lifecycle_state`**: crash-resumable managed-lifecycle state,
  written after every state-changing transition, restored on startup
  via `PositionLifecycleManager.restore_from_store` — though startup
  reconciliation against the broker's own live position/order state
  remains a named, undone gap (`docs/design/POSITION_LIFECYCLE.md`).

## Idempotency

- `command_ledger.idempotency_key` is UNIQUE; a retried call with the
  same key never submits a second broker order, and a differing
  `request_fingerprint` under a reused key is a hard error
  (`CommandFingerprintMismatch`), never silently resolved by guessing.
- `idempotency_records` gives the same guarantee for close/flatten
  HTTP endpoints: a retried request with the same idempotency key
  replays the stored result rather than re-executing.
- `export_events.event_id` is UNIQUE with `INSERT OR IGNORE`: an
  accidentally re-appended identical envelope is a harmless no-op, not
  a duplicate.

## No fabricated data

A pervasive, explicit convention throughout the schema and code
comments: a value this service cannot honestly know is represented as
`NULL` (or a documented "unresolved" marker/list), never a guessed or
default-zero stand-in that could be mistaken for a real observation.
Examples: `AccountBalance` fields are `None`, not `0.0`, when a broker
genuinely doesn't report them; `position_excursions.mae`/`mfe` are
`NULL` (not `0.0`) when there is no price observation to compute from;
`ExposureReport.unresolved_symbols` is surfaced rather than silently
folded into a `0.0` notional contribution.

## Auditability

- Append-only history tables — `stop_target_events`,
  `route_qualifications` — are never updated or deleted by any code
  path; a corrected fact is a new row, not an overwrite.
- Every SCHEMA-string table carries a real, load-bearing inline
  comment in `app/db.py` explaining what it is and why, maintained as
  living documentation alongside the DDL itself, not a separate
  document that can drift.
- `command_ledger` plus `orders` together give a complete pre-effect/
  post-effect trail for every financial command this service has ever
  attempted.

## Deployment/replication model

- SQLite file replicated across sites via Litestream
  (`deploy/RUNBOOK.md`); no independent database server to operate.
- Litestream replication lag/RPO is an explicitly disclosed limit: a
  promoted site's database may be missing the most recent writes, and
  neither `app/writer_lease.py` nor this document claims otherwise —
  `deploy/RUNBOOK.md` step 3's manual verification is the only
  safeguard.

## CI-verified quality gates

Every change to `signal-copier/**` runs, per
`.github/workflows/signal-copier-ci.yml`:

- `ruff check .` (lint) over the whole `signal-copier/` tree.
- `mypy` over a defined, CI-scoped file list (not the whole codebase —
  see `docs/requirements/ACCEPTANCE.md`).
- The full `pytest` suite.
- `pip-audit` against `requirements.txt` (with one disclosed, dated
  CVE exception — see the workflow file's own comment).
- A dedicated secret-scanning job (`gitleaks`).
