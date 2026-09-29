# Architecture

signal-portfolio-commercial is a multi-tenant, Postgres-backed FastAPI
service that turns the private, single-owner `signal-copier` execution
app into a commercial portfolio research, publication and customer-copy
platform. It never touches signal-copier's broker credentials or
account/flatten routes; the two systems are connected by exactly one
narrow, signed, append-only export relay.

## 1. The multi-tenant API layer (`app/api/`)

Two FastAPI routers, mounted by `app/main.py::create_app`, each bound to
a **different** database role/session factory:

- **`app/api/dashboard_routes.py`** (~3,450 lines) — every real,
  browser- and customer-facing screen: staff "ops" screens
  (`/ops/...`), the customer-facing app (`/app/...`), the public
  catalog/marketing site (`/`, `/portfolios`, `/pricing`, `/methodology`,
  `/help`, `/compare`), and local auth (`/auth/...`). Bound to
  `app.state.session_factory`, the ordinary `app_role` connection. Every
  handler resolves a `TenantScope` (`app/services/auth.py`) via
  `app/api/dependencies.py::get_current_scope` and calls
  `set_tenant_scope` before touching any tenant-scoped table — the RLS
  policy (below) is the real enforcement; the route handler's own
  tenant filtering is a convenience, not the only guard.
- **`app/api/relay_routes.py`** — exactly one route,
  `POST /internal/relay/ingest-batch`. Bound to
  `app.state.relay_session_factory`, a **separate engine** connected as
  `relay_role` (never `app_role`, never reused from the dashboard
  factory). This is the only HTTP route signal-copier's own relay
  worker ever calls. See §3.

`app/main.py` itself only adds `/healthz`, an authenticated
`GET /api/v1/me` (exercises the full JWT + tenant-scope stack with no
side effects), and `POST /api/v1/billing/webhook/stripe` (HMAC-verified
Stripe webhook receiver, idempotent via `record_event_if_new`).

Every screen is documented as either fully real or an honestly bounded
slice in `app/api/dashboard_routes.py`'s own module docstring — read
that first before assuming a screen does more (or less) than its route
actually implements.

## 2. The RLS-scoped Postgres persistence model

`app/db.py` is the single source of truth for the tenant-isolation
mechanism. Two Postgres roles exist:

- **`app_role`** (the ordinary browser/dashboard connection): can read
  and write every table, but every tenant-scoped table has
  `ENABLE + FORCE ROW LEVEL SECURITY` with a `tenant_isolation` policy
  that only ever permits rows matching
  `current_setting('app.tenant_id')` — **FORCE** matters because plain
  `ENABLE` is bypassed by the table owner, which is exactly the role
  most queries run as in this single-role-per-database setup. No
  `app.tenant_id` set means no visibility at all (fail-closed, never
  "everything").
- **`relay_role`** (the restricted relay-worker connection,
  `app/db.py::_apply_relay_role_access`): `LOGIN NOSUPERUSER
  NOBYPASSRLS`, granted only `SELECT` on `export_stream_registrations`
  (via a second, bespoke PERMISSIVE policy — Postgres ORs multiple
  permissive policies, so this widens nothing for any other role) plus
  `SELECT/INSERT/UPDATE` on `inbox_events` and `INSERT` on
  `ledger_entries`, both still gated by the ordinary `tenant_isolation`
  policy once `set_tenant_scope` has resolved a tenant. `relay_role` has
  **no command authority whatsoever** — it cannot submit, cancel or
  modify anything, and cannot read any other table.

`set_tenant_scope(session, tenant_id)` calls Postgres `set_config('app.tenant_id', ..., true)`
(session-local, cleared on commit) — every request/job that touches a
tenant-scoped table must call it with a real, authenticated tenant_id
first; a browser-supplied tenant_id is never trusted on its own.

Two tables get bespoke, non-generic RLS policies because they must be
visible to an **unscoped, anonymous** session too:
`products` (`product_visibility`: own tenant OR `lifecycle_state = 'PUBLISHED'`)
and `content_documents` (`content_document_visibility`: own tenant OR
`state = 'PUBLISHED'`) — the public catalog and methodology pages.

`ledger_entries`, `portfolio_versions`, `portfolio_version_sleeves` and
`audit_events` are additionally **append-only**: a `BEFORE UPDATE OR
DELETE` trigger (`forbid_ledger_mutation`) raises on any attempt to
mutate or remove a row, even by the table owner. A mistake is corrected
by inserting a new row that references the original (`correction_of`),
never by editing history.

## 3. The ingest inbox (`app/services/integration_inbox.py`)

The commercial side of the restricted relay
(`INTEGRATION_DECISION.md` S4.4/S6). `ExportStreamRegistration` is an
owner-controlled table binding a signal-copier `source_stream` to
exactly one tenant — an inbound envelope carries no tenant_id of its
own, so this table is the *only* place tenant binding comes from
(never trust the sender).

`ingest_export_event(session, envelope_json)`:
1. Resolves the tenant from `source_stream` (the one lookup `relay_role`
   can do unscoped), then calls `set_tenant_scope` immediately — every
   later read/write in the same call is protected by the ordinary
   per-tenant RLS policy.
2. Is **idempotent by `event_id`**: redelivering identical bytes is a
   harmless no-op; redelivering the same `event_id` with a *different*
   payload hash raises `EventIntegrityError` — never silently
   overwritten ("last write wins" is explicitly rejected).
3. Enforces **ordering per `(source_stream, producer_generation)`**:
   an event only applies (produces its real projection — a ledger
   entry, a routing-outcome update) once its `export_sequence` is next
   in line; an out-of-order arrival is durably received but left
   **parked** (unapplied), and applying one event cascades forward
   through anything already parked immediately behind it.
4. Detects **generation rollback/discontinuity**
   (`producer_generation` bumped when a stream is re-bootstrapped from a
   snapshot) and parks rather than silently applying old or
   unreconciled history.
5. Handles **bootstrap snapshots** (`POSITION_SNAPSHOT` manifest pages,
   received outside the ordinary sequence gate so a manifest can
   activate the instant its last page arrives) — once activated, a
   snapshot's `cutoff_sequence` becomes a hard floor so no event at or
   below it is double-counted.

Every honest disposition (`unsupported_schema_version`,
`unimplemented_event_type`, `generation_rollback_detected`,
`new_generation_requires_bootstrap`, `manifest_metadata_mismatch`,
`manifest_generation_mismatch`, or a plain out-of-order gap wait with no
named reason) is a real, visible `parked_reason` — nothing is ever
"best-effort" coerced into a ledger entry. `app/services/source_coverage.py`
turns this table into a per-tenant coverage report
(`ledger_recorded` / `parked` / `received_no_ledger_entry`, plus the
correlated `routing_outcome` once it arrives).

## 4. The four-book economic ledger (`app/models/ledger.py`, `app/services/ledger.py`)

A single **append-only** `ledger_entries` table spanning four
independent books (`Book` enum):

| Book | Meaning |
|---|---|
| `SOURCE` | what the provider/analyst originally recommended (not a claimed execution) |
| `MODEL` | the canonical portfolio-version model's own instructions |
| `PLATFORM` | the owner's actual discretionary/strategy account |
| `FOLLOWER` | a specific customer's actual executed account (authorized observation only) |

"An alert delivered is not an executed trade. C2 strategy model
performance is not customer actual performance" — the books are never
blended. Money fields are `Numeric(28,10)`, never `float`. Every entry
carries a required `evidence_class` (no honest default exists — a
caller must state whether this is synthetic, paper, hypothetical, a
real owner/follower observation, or a platform-reported result) and an
optional `fee` (`NULL` means *unknown*, never zero — a zero fee must be
asserted explicitly). `app/services/ledger.py::append_entry` /
`append_correction` are the only sanctioned writers; the append-only
trigger backs this up at the database level regardless.

Two replay-based reporting services read this ledger without ever
mutating it:

- **`app/services/platform_performance.py`** — one blended
  volume-weighted-average cost per (tenant, book, instrument): correct
  for current position/basis, but cannot recover which analyst
  contributed what once positions blend.
- **`app/services/analyst_attribution.py`** — a FIFO-lot replay (ported
  from signal-copier's own `provider_value.py` algorithm, reimplemented
  for `Decimal` correctness) that tags each opening fill's lot with its
  own `originating_analyst_id` and consumes lots oldest-first on a
  reducing fill, crediting realized P&L to *the lot's own* analyst —
  never the analyst on the closing fill, never split arbitrarily. Its
  per-analyst sum always reconciles exactly to
  `platform_performance`'s own aggregate. Also derives real "completed
  episodes" (one per fully-closed lot) for win-rate reporting — `None`
  win rate (not a fabricated 0%) until at least one episode closes.

## 5. Research/selection engine

`app/services/portfolio_research.py` (research-run declarations over a
real candidate `Sleeve` universe, with real combinatorics for the
candidate count), `app/services/candidate_comparison.py` (shadow/compare
report), `app/services/portfolio_selection.py` and
`app/services/portfolio_rights.py` (a `PortfolioVersion` is
rights-eligible only when **every** member sleeve independently
qualifies — one ungranted/revoked sleeve denies the whole version;
`PortfolioVersion`/`PortfolioVersionSleeve` are append-only, so
historical membership is never overwritten, only superseded by a new
version's own rows).

## 6. Publication / copy-mandate lifecycle

`app/models/publication.py` + `app/services/publication.py` /
`publication_admission.py`: a `PublicationIntent` is admitted (real
rights recheck at the effect boundary, real entitlement gate for
new-exposure actions only) before being handed to a per-platform
adapter (§ SYSTEM_CONTEXT.md). `app/services/publisher_writer_claim.py`
enforces "single publishing mode" via a real primary-key uniqueness
constraint on `(channel, external_strategy_id)`. On the customer side,
`app/models/copy_mandate.py` + `app/services/copy_mandate.py` model a
customer's own copy-mandate lifecycle (draft/manage/cancel), and
`app/models/platform_connection.py` is deliberately **declared only,
never a real authorized observation channel** yet — no code path
populates a real `Book.FOLLOWER` ledger entry today.

## 7. Auth: local password auth + revocable JWT sessions

`app/services/local_auth.py` — real Argon2 password hashing
(`pwdlib[argon2]`) and cookie-based web sessions (login/logout, each
producing a real `AuditEvent`) — this deployment's own local identity
provider, deliberately **not** a Supabase Auth integration (that is an
external deployment action outside this build).

`app/services/auth.py` issues and verifies JWTs (`TenantScope`:
`tenant_id`, `user_id`, `role`). Every issued token's `jti` is recorded
in `app/models/token_revocation.py::IssuedToken`
(`app/services/token_revocation.py::record_issued_token`); revocation
adds a row to `RevokedToken` and is checked on **every** verification
(`is_token_revoked`) — any lookup failure (DB error, timeout) is treated
as **revoked** (fail closed, never fail open). "Log out everywhere"
(`revoke_all_tokens_for_user`) denylists every currently-unexpired `jti`
this service ever recorded issuing for a user; a cookie-based web
session can be explicitly revoked, but an already-issued Bearer JWT that
was never routed through a recorded session cannot be enumerated (only
revoked individually once its `jti` is known).

Role/action authorization is a single explicit allow-list,
`app/services/permissions.py::_ALLOWED` — an action with no entry denies
**every** role, including `OWNER`. There is no role hierarchy or
inheritance; each of the ~40 gated actions lists its exact permitted
role set.

## See also

- `docs/architecture/SYSTEM_CONTEXT.md` — external actors and boundaries
- `docs/architecture/COMPONENTS.md` — per-module catalog
- `docs/architecture/DEPENDENCIES.md` — internal/external dependency graph
- `docs/architecture/DATA_FLOWS.md` — end-to-end sequence diagrams
- `docs/GLOSSARY.md` — domain terms (four books, RLS, parked_reason, etc.)
