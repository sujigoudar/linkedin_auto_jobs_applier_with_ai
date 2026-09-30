# ADR-0008: Append-only triggers on ledger and versioning tables

## Status

Accepted. Implemented in `alembic/versions/04c418cbb547_row_level_security_and_append_only_.py`
and `app/db.py::_apply_append_only`/`enforce_append_only`.

## Context

`app/models/ledger.py`'s own docstring states the requirement directly: "The
execution journal is append-only by identity with correction/reversal events;
projections can be rebuilt." A mistaken ledger entry must be corrected by appending
a new row whose `correction_of` points at the original — the original is never
edited or removed, so the entry that was actually booked at the time stays
reconstructable. The same reasoning applies to `portfolio_versions`/
`portfolio_version_sleeves` (`app/models/portfolio_version.py`: "Historical
membership is never overwritten" — a weight change is a new version's own rows,
never an edit to an old one's) and to `audit_events` (an audit trail that could be
edited after the fact is not a trail). An application-level convention alone (e.g.
"the service layer only ever calls `append_entry`/`append_correction`, never
`UPDATE`") is not enough: a future refactor, a bug, or a one-off script run directly
against the database could route around it and silently corrupt history with no
trace that it happened.

## Decision

Enforce append-only as an actual Postgres trigger, not merely a service-layer
convention:

- A single trigger function, `forbid_ledger_mutation()`, unconditionally raises on
  any invocation: `RAISE EXCEPTION 'this table is append-only: % is not permitted',
  TG_OP`.
- A `BEFORE UPDATE OR DELETE` trigger, `append_only_guard`, is installed on every
  append-only table — `ledger_entries`, `portfolio_versions`,
  `portfolio_version_sleeves`, `audit_events` (`app/db.py::_APPEND_ONLY_TABLES`) —
  applied in the same migration, and the same connection-scoped way, as the RLS
  policies of ADR-0001 (`_apply_append_only`, reusing Alembic's own still-open
  transaction so it can see the just-created, not-yet-committed tables).
- This holds even for the table owner and even for a caller who forgot (or a future
  refactor that removed) the application-level `append_entry`/`append_correction`
  discipline in `app/services/ledger.py` — the constraint is enforced at the
  database layer, independent of which role or code path is doing the writing.
- There is **no downgrade** for either the RLS or the append-only migration:
  `downgrade()` deliberately raises `NotImplementedError` rather than silently
  weakening a live database's isolation or immutability guarantees. An append-only
  table becoming mutable, or a tenant table losing row-level security, is never
  treated as a safe automatic "undo."

## Consequences

- No code path, present or future, authorized or not, can silently `UPDATE` or
  `DELETE` a row in an append-only table — attempting either always raises a real
  database error, not a swallowed no-op.
- Correcting a mistake is always a new row (`correction_of` set on `ledger_entries`,
  a new `portfolio_version_id` for a reweighted portfolio), which means every
  consumer of these tables (reporting, attribution, audit review) must be written to
  replay/fold history forward, never to assume it can patch a row in place.
- `alembic downgrade` cannot be used to reverse these two migrations automatically —
  reverting them (if ever genuinely intended) requires a deliberate, manually-
  reviewed operation, not a routine `alembic downgrade -1`.
