# Progress snapshot

Last updated from real commit history through `72efeae` (HEAD of
`claude/signal-copier-redesign` at the time this snapshot was written).
This is a point-in-time account — see `docs/history/ENGINEERING_LOG.md`
for the full narrative and `ops/commercial_state.json` /
`ops/COMMERCIAL_RESUME.md` for the original 13-phase build's own
detailed per-phase notes (now historical; much has been built since).

## What's real and tested, most recent first

- **Track 47: mutation-testing pass, PAMM/MAM pooled-account accounting
  (2026-10-01).** Widens Track 39's mutation-testing pass onto
  `app/services/pamm_accounting.py` (PAMM unit/NAV high-water-mark fee)
  and `app/services/mam_allocation.py` (MAM largest-remainder fair
  order allocation), per the user's instruction that mutation coverage
  needs to reach every module, highest financial-risk first. Found and
  closed real survivors: `pamm_accounting`'s dealing-price validity
  boundary had only the rejected side (`<= 0`) tested, letting an
  `<= 1` off-by-one mutant survive; `mam_allocation`'s zero-fillable-
  units and sub-1 total-weight boundaries had the same gap on two more
  mutants, and -- the most significant finding -- the per-account cap
  check inside the largest-remainder redistribution loop had its
  `continue` (skip a capped account, keep handing the remainder to the
  next one) survive a mutation to `break` (abandon redistributing
  entirely), because no existing test put a capped account FIRST in
  priority order while units still remained; a wrong allocation here
  would be a real money-misallocation risk once PAMM/MAM wiring goes
  live. No production code changed -- every survivor was a test gap,
  closed with a new test, none weakened or deleted. Final scores:
  `pamm_accounting.py` 8/12 (66.7%, remaining 4 all cosmetic exception-
  message string mutations); `mam_allocation.py` 42/45 (93.3%,
  remaining 3: 2 cosmetic string mutations, 1 confirmed equivalent --
  a loop-exit `break`/`continue` swap that produces an identical final
  result once `remaining` reaches 0, since nothing thereafter changes
  it).

- **Track 48: mutation-testing pass -- `publication_admission`,
  `customer_billing` (2026-10-01).** Widens Track 39's mutation-testing
  pass to the two highest financial-risk modules outside that
  original scope: `app/services/publication_admission.py` (the real
  effect boundary admitting a `PublicationIntent` for live
  publication) and `app/services/customer_billing.py` (CU-11's
  tenant-scoped billing read, which had no dedicated test file at all
  before this track). Same ad hoc `mutmut run
  --paths-to-mutate=<module>` approach as Track 39, not a persisted
  config. `publication_admission`: started 8/12 killed; the 4
  survivors were all cosmetic string-literal mutations inside two
  raised exceptions' diagnostic messages, closed by asserting the full
  exact message text (a substring check can still pass a
  prefix/suffix-padding mutation) -- final 12/12 killed (100%).
  `customer_billing`: new `tests/test_customer_billing.py` (9 tests);
  first run 9/10 killed, with one real gap closed (nothing had
  asserted the dataclass's own `frozen=True` immutability guarantee) --
  final 10/10 killed (100%). No equivalent mutants in either module.
  This module's own docstring is explicit it has no charge-amount
  math, proration, or duplicate-charge logic at all (no `Invoice`
  model, no real Stripe transport yet), so that part of this track's
  financial-risk framing doesn't apply to it as it exists today; the
  real risk covered is a billing-state read silently
  granting/denying entitlement across tenants or subscription states.
  Full suite: `1027 passed, 0 failed`; `ruff`/`mypy` both clean.

- **Fix: NUL-byte signin/signup crash (2026-10-01).** Closes the gap
  Track 39's fuzzing found and Track 41 flagged (unfixed, outside its
  own scope) in `docs/KNOWN_ISSUES.md`: a signin/signup form body with
  an embedded NUL byte crashed with a raw, unhandled
  `psycopg.DataError` instead of a clean 4xx. `app/services/
  local_auth.py`'s new `_reject_nul_bytes` guard, called from
  `authenticate`/`create_account`/`request_password_reset` before any
  email/password reaches a query, closes it treating a NUL byte
  exactly like an ordinary bad-credentials/unknown-email case.
  `tests/test_c39_schemathesis_api_fuzzing.py::
  test_public_signin_signup_never_5xx_on_adversarial_form_bodies`
  passes; full suite: `1018 passed, 0 failed`.

- **Track 42: honest `"applied"`/`"parked"` relay ingest status.**
  Closes the gap Track 40 found and flagged in `docs/KNOWN_ISSUES.md`:
  `POST /internal/relay/ingest-batch` now reports `"parked"` with a
  real `parked_reason` for a genuinely parked event, instead of
  mislabeling it `"applied"`. New
  `tests/test_c42_relay_ingest_honest_park_status.py`; updated
  `tests/test_c40_relay_ingest_adversarial_payloads.py`'s own
  out-of-order test to assert the real status. Cross-repo: signal-
  copier's own `app/relay_worker.py` now handles a `"parked"` status
  for real (transient vs. structural, per its own CHANGELOG.md entry)
  instead of silently falling through unrecognized.

- **Track 41: ledger correctness (2026-10-01)** -- closed two real gaps
  Tracks 35/40 flagged. (1) `EventType.SOURCE_RECEIPT` edit
  double-booking: an edited instruction used to book a second,
  independent `Book.SOURCE` entry instead of superseding the original
  (signal-copier dedupes an edit by exact `(channel_id, message_id,
  revision_id)`, never reusing the original `Signal.id`) --
  `source_event_native_key` is now also set on `SOURCE_RECEIPT` rows,
  and a revision (`source.original_source_event_id` set) resolves
  against the earlier receipt it supersedes, booking a correction
  (`append_correction`, extended with optional `instrument`/`side`/
  `currency`/`multiplier` overrides) with the revised values instead of
  a new entry. Every ledger-history reader (`compute_book_performance`,
  `compute_analyst_attribution`, `compute_customer_equity_series`) now
  resolves a root entry's real values through a new shared
  `platform_performance.effective_fill` helper. (2) `TARGET_UPDATE`/
  `STOP_UPDATE` real representation (ADR-0011): a new, dedicated,
  append-only `source_stop_target_revisions` table -- never new
  `LedgerEntry` columns, since a stop/target revision carries no
  quantity/price economic fact of its own. Both kinds now apply and
  advance their stream instead of parking forever. Full suite passing
  against a real disposable Postgres cluster; see `CHANGELOG.md` for
  the full list of files touched.
- **Track 38 property-based/stateful test hardening** -- two new test
  files, both passing against a real disposable Postgres cluster:
  `tests/test_trk38_edit_correlation_hypothesis.py` (60 generated cases
  proving Track 35's EDIT-kind SourceEvent correlation never produces a
  cross-tenant false-positive match, including out-of-order delivery)
  and `tests/test_trk38_rls_ledger_and_inbox.py` (6 tests extending the
  real, non-superuser `app_role` unfiltered-query RLS proof pattern to
  `LedgerEntry` and `InboxEvent`, the two financially load-bearing
  models that had no such proof yet). No real bug found; full suite
  remains 983 passed, 0 failed.
- **Track 39: mutation-testing pass (2026-10-01)** — ran `mutmut`
  against `app/services/trading_authority.py` and
  `app/services/ledger.py` (scoped to each module's own dedicated test
  file(s)) to measure whether the existing tests actually catch a real
  injected bug, not just whether they happen to pass.
  `app/services/release_taxonomy.py` was also scoped but has zero
  mutable mutation points (a pure, total dict-lookup table) — already
  fully covered. Found and closed real survivors: `trading_authority`'s
  `applicable`/per-check diagnostic fields were asserted only at the
  first couple of its six early-return gates, and — the most
  significant finding — a REJECTED review, a different product's
  APPROVED review, and a stale prior revision's APPROVED review could
  each have been mistaken for "this product's current approved
  release review" if the query's own filter were ever regressed; now
  explicitly tested against all three. `ledger`'s `append_correction`
  had its explicit-new-fee override path (the real FEE-event call
  path from `app/services/integration_inbox.py`) completely untested —
  only the "inherit from original" branch was ever exercised. No
  production code changed; every real survivor was a test gap, closed
  with a new test. Final scores: trading_authority.py 180/188 (95.7%,
  remaining 8 either cosmetic multi-item string-join formatting or
  incident-query filter drops that only widen which incidents block —
  the fail-safe direction this gate's design already favors);
  ledger.py 82/82 (two `reconciliation_state=None` mutants are
  confirmed equivalent — the column itself is `nullable=False,
  default=UNRECONCILED`, so the database backstops an explicit `None`
  regardless).
- **Release taxonomy + trading-authority qualification gate
  (`GET /system/readiness`)** -- see ADR-0010
  (`docs/adr/0010-release-taxonomy-and-trading-authority-qualification-gate.md`).
  This route, and both fields it reports, did not exist before this
  track; it is new, real, computed work, not a stub replacement.
  `release_status` renames the existing `Product.lifecycle_state`/
  `ReleaseReview` pipeline onto `RESEARCH_ONLY -> SHADOW ->
  LIMITED_LIVE -> FULLY_RELEASED`. `trading_authority`
  (`app/services/trading_authority.py`) is a fail-closed gate checking
  publication, release-review approval, order-routing-specific rights
  (`AUTOMATED_PUBLICATION`/`MANAGED_ACCOUNTS`, not the alerts grant),
  and open high/critical incidents -- and, once every one of those
  passes, correctly reports `missing_input:execution_activation_
  pipeline` rather than a fabricated qualified pass, since neither
  `CopyMandate` nor `ManagedProgram` has a real ACTIVE/enrolled-live
  state in this build yet. 18 new tests.
- **Revocable JWT sessions** (`72efeae`) — Bearer-token JWTs were
  previously cryptographically self-contained and unrevocable before
  natural expiry. Now: `issued_tokens`/`revoked_tokens` tables
  (`app/models/token_revocation.py`, Alembic `c1d2e3f4a5b6`) back a
  real, DB-checked denylist. `issue_token` mints a fresh `jti` per
  token; `verify_token` (the one real request-verification call site,
  `app/api/dependencies.py`'s `get_current_scope`) checks the denylist
  after decode and **fails closed** — any exception during the
  revocation check is treated as rejection, never as "not revoked."
  Two real revoke actions: a customer's own "revoke all API tokens"
  (CU-13) and a staff member's per-user revoke (AD-16), both logged as
  a real `AuditEvent`. 5 new tests, end-to-end HTTP coverage including
  two fail-closed proofs.
- **AD-18 evidence manifest export** (`f752904`) — a real, synchronous
  JSON export of filtered `AuditEvent` rows plus a manifest header
  (generated-at/by, filter criteria, row count, a content hash via
  `signal_platform_contracts.compute_payload_hash`). Gated by a new
  `export_evidence_manifest` permission (same OWNER/REVIEWER pair as
  `view_audit_log`); the export itself is logged as a new `AuditEvent`.
  Load-bearing tamper-detection test: the content hash changes when a
  bundled row is altered.
- **Secret rotation — CURRENT+PREVIOUS dual-secret verification**
  (`9ddc681`) — both `catalog-fit-sim` (signal-copier) and relay
  (signal-portfolio-commercial) signature verification now accept
  either the CURRENT or an optional PREVIOUS configured secret
  (`hmac.compare_digest`, same constant-time comparison as before),
  enabling zero-downtime secret rotation. Fails closed when both are
  unset.
- **CU-06 real max drawdown + win rate** (`64d596d`) — a real
  chronological cumulative-realized-P&L equity series from a
  customer's own `Book.FOLLOWER` ledger entries (never a fabricated
  indexed-to-100 return series — this build tracks no real
  starting-capital baseline), max drawdown ported directly from
  `signal-copier/app/statistics.py::compute_max_drawdown` (not
  reimplemented), and a completed-episode win rate reusing
  `analyst_attribution.py`'s own FIFO-lot open/close tracking
  (INT-026). Fixed a real peak-tracking regression left mid-
  verification by a prior session; added a regression test
  specifically shaped to catch that bug class.
- **INT-027 routing/admission/fill outcome encoding** (`e95fc35`
  precedes; `e95fc35`/`e635...` line) and **PU-03 fit-simulator equity
  curve as a real Chart.js line chart** (`e95fc35`) — the public "Try
  our fit simulator" panel's real `equity_curve` (from signal-copier's
  fit-simulation response) is now actually plotted, using the same
  vendored Chart.js `signal-copier` already ships, mounted the same
  way. Honest empty state when there are zero fitting trades.
- **Session audit wiring** (`76cf4d9`) — real session audit wired into
  AD-16/AD-11, AD-01 incident linking confirmed.
- **Customer-portal screens** (`fc25d37`) — CU-04/CU-05/CU-06/CU-11/
  CU-15 built.
- **AD-06/AD-21** (`956bedb`) — candidate comparison/draft and
  commercial incidents.
- **Local auth system** (`e303af9`) — real sign-in/sign-up/verify/
  recovery (ID-01/ID-02/ID-03): argon2id password hashing (`pwdlib`),
  single-use expiring email-verification/reset tokens, cookie-based web
  sessions with a separate CSRF token per mutation. Email delivery is
  not wired (no SMTP/SendGrid account exists) — the link is shown
  directly on the page instead, the same disclosed-gap pattern used
  elsewhere. Self-service signup is scoped to `CUSTOMER`, never
  `OWNER`.
- **Integration relay/inbox pipeline** (slices 1–13, `c244dbd` through
  `0f14efe`) — `signal_platform_contracts`, the restricted relay
  worker and `relay_role` boundary, ordering/gap detection, producer-
  generation binding, per-analyst FIFO-lot P&L attribution, real
  end-to-end late-fee correction, a rendered Trading & Integration
  Status page, an acceptance-case verification pass against the
  integration pack's own 40 cases.
- **INT-001 real single-command installation** (`777dee1`, plus fix
  commits `2c1079a`, `43e1ac0`) — `docker compose up --build` brings up
  both real apps, Postgres, and the in-process relay, entirely in
  `LOCAL_SIM`/paper mode. The two fix commits exist because the
  initial claim of "done" didn't survive actually running the
  containers — a real, demonstrated example of why
  `docs/process/DEFINITION_OF_DONE.md`'s bar matters.

## What predates this window (the original 13-phase build)

Rights registry, tenancy/RLS/permissions, the four-book append-only
ledger, portfolio research (deterministic half only), publication
intent write-path, Collective2/eToro adapter request-shape mapping
(never transmits), subscriptions/entitlement/Stripe-webhook
verification (never a real Stripe call), onboarding state machine,
PAMM/MAM simulation-only accounting, the model-gateway permission
boundary (no real LLM provider called). See
`ops/COMMERCIAL_RESUME.md` and `ops/commercial_state.json` for the
full, honest per-phase account, and `docs/12_validation_report.md` /
`docs/12_addendum_post_phase12_work.md` for the requirement-by-
requirement audit those phases were checked against.

## Track 40: fuzzing/fault-injection coverage (new for this repo)

No Schemathesis/Toxiproxy-style coverage existed for this repo before
this track (signal-copier already had both -- its own C30/C32). Added,
following that exact established pattern rather than inventing a new
one:
- `tests/test_c39_schemathesis_api_fuzzing.py` -- first Schemathesis
  pass, scoped to the highest-exposure routes: the relay/Stripe-webhook
  ingress routes, `GET /health`/`/system/readiness`/`/api/v1/me`, and
  the ID-01/ID-02/ID-03 `/auth/signin`/`/auth/signup` routes.
- `tests/test_c40_relay_ingest_adversarial_payloads.py` -- adversarial
  payloads against the real relay ingress route (wrong content-type,
  truncated/deeply-nested/oversized JSON, unicode/embedded-NUL/
  injection-style strings, duplicate event_id with a different body,
  out-of-order sequences). Found and fixed two real unhandled-500 bugs
  in `app/api/relay_routes.py` (an uncaught `pydantic.ValidationError`/
  `sqlalchemy.exc.DataError` could crash an entire batch request) --
  see CHANGELOG.md for the full detail. Also found and FLAGGED (not
  fixed -- a cross-service wire-contract design question, see
  `docs/KNOWN_ISSUES.md`) that the route's per-event `"status":
  "applied"` label is inaccurate for a genuinely parked event.
- `tests/test_c38_db_connection_drop_fault_injection.py` -- the
  highest-value check given this is financial accounting software: a
  real `pg_terminate_backend()` kills a session's backend mid-
  transaction, after a real ledger write but before `COMMIT`, proving
  Postgres's own rollback leaves no partial ledger state and the same
  envelope redelivers and applies cleanly afterward.

A note on verifying this: this repo's `postgres_cluster` fixture
assumes one pytest invocation running alone against `/tmp/pytest-of-
root/`'s shared, incrementing directory numbering -- running multiple
overlapping `pytest` invocations against this repo at once (including
from an unrelated concurrent session on a shared machine, e.g. another
agent's mutation-testing run) can cause pytest's own temp-dir retention
cleanup to delete a still-live cluster's data directory out from under
it, which looks exactly like a flaky/corrupted suite but has nothing to
do with any one test's own correctness. Verify with a dedicated
`--basetemp` (outside `/tmp/pytest-of-root/`) if anything else might be
running `pytest` against this repo concurrently.

## Current scale (approximate, from this snapshot)

- 43 Alembic revisions (`alembic/versions/`).
- ~15,600 lines across `app/`.
- 941 tests across `tests/` (`pytest -q`, re-verified 2026-10-01 against
  HEAD `9ab1104`: 941 passed, 0 failed), all run against a real
  disposable Postgres cluster. Track 40 added 17 more across its three
  new test files (993 total, isolated full-suite re-run: 993 passed, 0
  failed -- see above).
- CI: lint (ruff) + type check (mypy) + `pytest -q` + a dedicated
  `alembic upgrade head` verification step against a second, fresh
  disposable cluster.

## What is still genuinely open

See `docs/KNOWN_ISSUES.md`, `docs/TECH_DEBT.md`, and
`docs/PENDING_DECISIONS.md` — PAMM/MAM remains simulation-only,
Collective2/eToro/CopyFactory adapters build request shapes but never
transmit (no real credentials exist), Stripe webhook verification is
real but no real Stripe account or processor call exists, and six
owner-only action cards (legal entity, source rights, platform
agreements, payment processor, customer agreements, go-live decision)
remain outstanding and cannot be advanced by more coding.
