# Changelog

All notable changes to `signal-portfolio-commercial`, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) style. This
app has no tagged releases — see `docs/process/RELEASE.md` — so
entries are grouped by date instead of version. Dates and short hashes
are real, from `git log -- signal-portfolio-commercial`.

## [Unreleased]

Everything in this file. This is pre-1.0, development-branch software;
nothing here has shipped to a live production deployment
(`docs/process/RELEASE.md`).

### 2026-10-01 — Track 47: mutation-testing pass (PAMM/MAM pooled-account accounting)

Widens Track 39's mutation-testing pass (originally scoped to
`trading_authority.py`/`ledger.py`) onto the PAMM/MAM pooled-account
allocation and accounting modules, per the user's instruction that
mutation coverage needs to cover every module, highest financial-risk
first: `app/services/pamm_accounting.py` (PAMM unit/NAV high-water-mark
fee math) and `app/services/mam_allocation.py` (MAM largest-remainder
fair order allocation). Ran `mutmut` against each module, scoped to
its own dedicated test file, same ad hoc CLI pattern as Track 39 (no
persisted `setup.cfg`/`pyproject.toml` config).

#### Added
- `tests/test_pamm_accounting.py`: `units_for_cashflow`'s positive-
  dealing-price validation was only ever tested at a clean price
  (`10`) and at the rejected boundary (`0`, `-5`) -- a real mutant
  (`dealing_nav_per_unit <= 1` instead of `<= 0`) survived because
  nothing exercised a VALID price at or below 1 (e.g. a dealing price
  of exactly `1`, or a fractional price like `0.5`). This is exactly
  the kind of off-by-one on a financial validity boundary that would
  silently reject legitimate low-priced-unit cashflows. New
  `test_a_dealing_price_at_or_below_one_but_still_positive_is_accepted`
  closes it. Mutation score: 8/12 killed (66.7%); the 4 remaining
  survivors are all cosmetic string-literal mutations (exception
  message text, an unused `Decimal("0.01")` string-padding mutation
  never asserted against) -- same category Track 39 also left as
  equivalent/non-load-bearing.
- `tests/test_mam_allocation.py`: four real gaps closed.
  1. `allocate_fills`'s negative-fillable-units guard (`< 0`) had no
     test at the `0` boundary, so two off-by-one mutants (`<= 0` and
     `< 1`) both survived by wrongly rejecting the legitimate "zero
     units to allocate" case. New
     `test_zero_fillable_units_is_valid_not_an_error`.
  2. The positive-total-weight guard (`<= 0`) had no test with a
     weight sum that is positive but less than 1 (account weights are
     relative, never required to sum to 1), so a `<= 1` mutant
     survived. New
     `test_a_fractional_total_weight_below_one_is_still_a_positive_weight`.
  3. The real money-misallocation finding: in the largest-remainder
     redistribution loop, the per-account cap check's `continue` (skip
     this capped account, keep trying the next one in priority order)
     mutated to `break` (abandon redistributing the remainder
     entirely) survived -- meaning the existing cap test never put a
     *capped* account *first* in priority order while units still
     remained to distribute. New
     `test_a_capped_account_first_in_priority_order_is_skipped_not_a_stop`
     constructs exactly that scenario (three equal-weight accounts,
     account `"a"` -- first by the ascending tie-break -- already at
     its cap) and asserts the remainder correctly flows to `"b"`
     instead of being stranded as `unallocated_units`.
  4. `MamAllocationResult`'s `frozen=True` (immutability, since an
     allocation result is meant to be an immutable audit artifact) and
     its `remainders` field's `default_factory=dict` (vs. `None`, were
     a future caller ever to construct the dataclass directly without
     passing `remainders`) were asserted nowhere. New
     `test_mam_allocation_result_is_immutable` and
     `test_mam_allocation_result_remainders_defaults_to_an_empty_dict`.
  Mutation score: 42/45 killed (93.3%). Of the 3 remaining survivors,
  2 are the same cosmetic string-literal category as above
  (`ValueError` message text); the third -- the loop-exit mutant
  `if remaining <= 0: break` mutated to `continue` -- is a confirmed
  **equivalent mutant**: once `remaining` reaches 0 it never changes
  again inside that branch, so every subsequent loop iteration just
  re-triggers the same `continue` with no side effect, producing an
  identical final `allocations`/`unallocated_units` to `break`; only
  the (unobservable) iteration count differs.

No production code changed in either module -- every real survivor
was a genuine test gap, closed with a new, targeted test; no existing
test was weakened or deleted.

### 2026-10-01 — Track 48: mutation-testing pass (publication_admission, customer_billing)

Widens Track 39's mutation-testing pass (which scoped
`trading_authority.py`/`ledger.py`) to the two highest financial-risk
modules outside that original scope: `app/services/
publication_admission.py` (the real effect boundary admitting a
`PublicationIntent` for live publication -- CP-003/CP-051) and
`app/services/customer_billing.py` (CU-11's tenant-scoped billing
read). Same ad hoc approach as Track 39: `mutmut run
--paths-to-mutate=<module>` scoped to each module's own dedicated test
file(s), not a persisted config.

#### Added
- `tests/test_publication_admission.py`: the existing five tests
  already exercised every branch (rights-denied, entitlement-denied,
  risk-reducing management bypassing entitlement, writer-claim
  conflict), but asserted only the exception *type* raised, never the
  diagnostic *message* text. Mutation score started at 8/12 killed
  (66.7%); the 4 survivors were all cosmetic string-literal mutations
  inside the two raised exceptions' messages (`RightsDeniedAtAdmission
  Error`/`EntitlementDeniedAtAdmissionError`). Closed by asserting the
  full, exact message text (not a substring `in` check, which a
  prefix/suffix-padding mutation can still satisfy) in the two tests
  that trigger those denials -- this is a real diagnosability gap on a
  path an ops engineer relies on to understand *why* a portfolio/
  subscription was blocked from live publication, not a cosmetic nit.
  Mutation score after: 12/12 killed (100%); no equivalent mutants.
- `tests/test_customer_billing.py` (new file -- `get_own_billing_state`/
  `CustomerBillingState` had no dedicated test file at all before this
  track; only incidental coverage via `app/models/billing.py`'s own
  persistence tests). Nine tests covering: empty state, `current`
  picking by `created_at` recency (never insertion order, per the
  dataclass's own docstring on "more than one terminal-state row can
  coexist"), `authorizes_new_entry`/`authorizes_risk_reducing_
  management` delegation across ACTIVE_PAID/PAST_DUE/PENDING_PAYMENT
  states, explicit tenant-scoping in the query itself (not just RLS as
  the only backstop) under both a bypassed and a real RLS session, and
  the dataclass's own `frozen=True` immutability guarantee. Mutation
  score: first run 9/10 killed (90%) -- the one survivor was a real gap
  (nothing asserted that the frozen dataclass actually raises
  `FrozenInstanceError` on mutation), closed with a dedicated test.
  Final score: 10/10 killed (100%); no equivalent mutants. Note: this
  module's own docstring is explicit that it has no charge-amount math,
  proration, or duplicate-charge logic at all -- there is no `Invoice`
  model and no real Stripe transport in this build
  (`app/services/stripe_webhook.py`'s own docstring) -- so the
  "charge-amount math" financial-risk framing this track's scope was
  given does not apply to this module as it exists today; the real
  risk here is a billing-state read silently granting/denying
  entitlement across tenants or subscription states, which the new
  tests cover directly.

Practicality note: `mutmut`'s own test-per-mutant subprocess model (a
fresh disposable Postgres cluster per `db_session` fixture call, per
`tests/conftest.py`'s own docstring) made each mutation run noticeably
slower in this shared, concurrently-loaded sandbox than Track 39's
original pass -- several runs needed retries after transient
Postgres-fixture permission races from *other* concurrent agents'
mutation-testing runs sharing the same `/tmp`, and one `pip install
mutmut` from another concurrent session briefly upgraded the shared,
non-venv `mutmut` install out from under this track's own run (fixed by
pinning `mutmut==2.5.1` in a dedicated venv for the rest of this track).
Full suite: `1027 passed, 0 failed`; `ruff check .` and `mypy app
--ignore-missing-imports` both clean.

### 2026-10-01 — Track 42: honest `"applied"`/`"parked"` relay ingest status

Closes the honesty gap Track 40 found and flagged (never fixed) in
`docs/KNOWN_ISSUES.md`: `POST /internal/relay/ingest-batch`
(`app/api/relay_routes.py`'s `ingest_batch`) reported `"status":
"applied"` for every event in a batch that didn't raise one of the
route's named exceptions — including a genuinely PARKED event (a
sequence gap, an unsupported `schema_version`, an unimplemented
`event_type`, or any other `PARKED_REASON_*` from
`app/services/integration_inbox.py`).

#### Fixed
- `app/api/relay_routes.py`: `ingest_batch` now reports the real
  outcome per event — `{"status": "applied", "event_id": ...}` only
  when `InboxEvent.applied_at` is genuinely set, otherwise
  `{"status": "parked", "event_id": ..., "parked_reason": ...}` with
  the real `parked_reason`, or the explicit
  `"sequence_gap_awaiting_predecessor"` for the one park case that
  carries no named reason of its own.

#### Added
- `tests/test_c42_relay_ingest_honest_park_status.py`: HTTP-level
  coverage of the real `"applied"` vs `"parked"` contract, including a
  structurally-unrepresentable `SourceEventKind.TARGET_UPDATE` and an
  unimplemented `EventType`.
- Updated
  `tests/test_c40_relay_ingest_adversarial_payloads.py::test_out_of_order_sequence_in_one_batch_parks_then_resolves_without_500`
  to assert the real, honest `"parked"` status it used to document as
  a known gap rather than fix.

#### Cross-repo
- signal-copier's own `app/relay_worker.py` is the one caller of this
  endpoint; see its own CHANGELOG.md entry for how it now handles a
  `"parked"` status (never conflating it with `"applied"`), split by
  `parked_reason` into transient (retried) and structural
  (terminally-parked-and-surfaced) cases.

#### Judgment call (recorded per CLAUDE.md rule 3)
The response shape stays an ad-hoc dict on both sides rather than a
new `signal_platform_contracts` type — four keys, one real caller,
already fully specified in this endpoint's own module docstring and
now in `docs/KNOWN_ISSUES.md`; a shared, versioned contract type would
be more process than this fix needs.

### 2026-10-01 — Fix: NUL-byte signin/signup crash

Closes the gap `docs/KNOWN_ISSUES.md` flagged (found by Track 39's
fuzzing, left unfixed by Track 41 as outside its own scope):
`tests/test_c39_schemathesis_api_fuzzing.py::
test_public_signin_signup_never_5xx_on_adversarial_form_bodies` — a
signin/signup form body with an embedded NUL byte (e.g. `email=%00`)
reached `UserIdentity.email == email` and crashed with a raw,
unhandled `psycopg.DataError`/`sqlalchemy.exc.DataError` (Postgres
`text` columns reject an embedded NUL byte outright), a 500 instead of
a clean 4xx/401.

#### Fixed
- `app/services/local_auth.py`: new `_reject_nul_bytes` helper, called
  from `authenticate`, `create_account`, and `request_password_reset`
  before any email/password reaches a query — a NUL byte is treated
  exactly like an ordinary bad-credentials/duplicate-account/unknown-
  email case (the same error type/`None` each caller already returns
  for that), so this closes no email-enumeration signal of its own.

### 2026-10-01 — Track 41: ledger correctness (SOURCE_RECEIPT edit supersession; TARGET_UPDATE/STOP_UPDATE representation)

Closes two real gaps Tracks 35/40 flagged in `docs/KNOWN_ISSUES.md`.

#### Fixed
- **SOURCE_RECEIPT edit double-booking**: an analyst's edited
  instruction used to book a SECOND, independent `Book.SOURCE` ledger
  entry instead of superseding the original, because signal-copier
  dedupes an edited message by exact `(channel_id, message_id,
  revision_id)` rather than reusing the original `Signal.id`.
  `app/services/integration_inbox.py`'s `SOURCE_RECEIPT` branch now
  sets `InboxEvent.source_event_native_key` on every receipt (not only
  `SOURCE_EVENT` rows) and, when a receipt's own `source.
  original_source_event_id` resolves to an earlier, already-booked
  receipt via that exact tenant-scoped native-key mechanism (Track
  35's), books a correction with the revised quantity/price/
  instrument/side instead of a new entry. Unresolvable revisions park
  as `edit_without_resolvable_target:<key>`, never guessed.

#### Added
- `app/services/ledger.py::append_correction` gained optional
  `instrument`/`side`/`currency`/`multiplier` overrides (default
  `None` = inherit the original, unchanged — `EventType.FEE`'s own
  call site is unaffected).
- `app/services/platform_performance.py::EffectiveFill`/
  `effective_fill` — the one place every ledger-history reader now
  resolves a root entry's real (possibly corrected) economic fields;
  `compute_book_performance`, `app/services/analyst_attribution.py::
  compute_analyst_attribution` (now replays `load_ordered_root_entries`
  directly instead of its own separate query), and
  `app/services/customer_performance_report.py::
  compute_customer_equity_series` all use it.
- **TARGET_UPDATE/STOP_UPDATE real representation** (ADR-0011): a new,
  dedicated, append-only, tenant-scoped table,
  `source_stop_target_revisions`
  (`app/models/source_stop_target_revision.py`,
  `app/services/source_stop_target_revisions.py`, Alembic revision
  `b1f4d8a2c6e3`) — never new `LedgerEntry` columns (a stop/target
  revision carries no quantity/price economic fact of its own). Wired
  into `_apply_projection`'s `SOURCE_EVENT` branch: both kinds now
  apply and advance their stream instead of parking forever as
  `source_event_kind_not_ledger_representable:<kind>` (retired —
  no code path produces it any more). Best-effort correlation to the
  `Book.SOURCE` entry a revision concerns, via `source.
  parent_event_id`, is never required to apply (no money is at stake).
  `relay_role` gets `INSERT` on the new table too (Alembic revision
  `c7e2f91a4d05`, exercised end-to-end in
  `tests/test_relay_role_access.py`).
- `tests/test_ledger.py`, `tests/test_integration_inbox.py`,
  `tests/test_platform_performance.py`,
  `tests/test_analyst_attribution.py`: real coverage for both fixes,
  including a concrete ORIGINAL-then-EDIT-changing-quantity scenario
  proving only one net economic fact reaches `compute_book_
  performance`/`compute_analyst_attribution`, with the correction
  trail intact and inspectable; and real apply-and-read-back coverage
  for `TARGET_UPDATE`/`STOP_UPDATE`.

#### Docs
- `docs/adr/0011-source-stop-target-revision-history.md` (new).
- `docs/KNOWN_ISSUES.md`, `docs/design/LEDGER_MODEL.md`,
  `docs/database/SCHEMA.md`, `docs/database/DATA_DICTIONARY.md`
  updated to reflect both closed gaps.

### 2026-10-01 — Track 39: mutation-testing pass

Ran `mutmut` against `app/services/trading_authority.py` and
`app/services/ledger.py`, scoped to each module's own dedicated test
file(s), to measure whether the existing tests actually catch a real
injected bug. `app/services/release_taxonomy.py` was also scoped but
turned out to have zero mutable mutation points (a pure, total
dict-lookup table over `ProductLifecycleState`) — already fully
covered by its own 5-test file, nothing to add.

#### Added
- `tests/test_trading_authority.py`: `assess_trading_authority`'s
  `applicable`/`checks` fields were asserted only at the FIRST couple
  of its six early-return gates; added coverage at every remaining one
  (no-approved-review, no-execution-rights, blocking-incident), plus
  two real production-logic gaps the fix closed: a REJECTED review for
  the product's current revision, and a genuinely APPROVED review
  belonging to a DIFFERENT product (or a now-stale prior revision of
  the SAME product), must never be mistaken for "this product's
  current approved review" — both are exactly the kind of query-filter
  regression that could silently grant trading authority it shouldn't.
  Mutation score: 180/188 killed (95.7%); the 8 remaining survivors are
  either cosmetic string-join formatting in multi-item diagnostic
  strings, or query-filter drops on the blocking-incident check that
  only WIDEN which incidents count as blocking (the fail-safe
  direction this module's own "fail closed" design already biases
  toward — a false reject, never a false authorization).
- `tests/test_ledger.py`: `append_entry`/`append_correction` had no
  test setting `multiplier`/`follower_connection_id`/
  `originating_analyst_id`/`sleeve_id`/`external_observation_id` to a
  real, non-default value, and `append_correction`'s explicit-new-fee
  override (the real `app/services/integration_inbox.py` FEE-event
  call path) was entirely untested — only its "inherit from original"
  branch was ever exercised. Mutation score: 82/82 killed (two
  `reconciliation_state=None` mutants are confirmed equivalent: the
  underlying `LedgerEntry.reconciliation_state` column is declared
  `nullable=False, default=UNRECONCILED`, so SQLAlchemy backstops an
  explicit `None` at the DB layer regardless).

#### Added
- Track 38 (property-based test hardening):
  - `tests/test_trk38_edit_correlation_hypothesis.py` -- a Hypothesis
    property test for Track 35's EDIT-kind SourceEvent correlation
    (`_source_event_native_key` / the EDIT branch of `_apply_projection`
    in `app/services/integration_inbox.py`). Generates 60 random cases
    of ORIGINAL/EDIT/duplicate events, including the EDIT delivered
    BEFORE its own ORIGINAL (out-of-order export delivery) and two
    tenants sharing an identical `source_provider_id`/`source_channel_id`/
    `source_event_id` triple, and proves correlation never produces a
    false-positive match: only ever a safe no-op-advance against a real
    same-tenant original, or an honest park -- never a cross-tenant
    resolution. Supplements the four hand-picked EDIT tests already in
    `tests/test_integration_inbox.py`. No bug found.
  - `tests/test_trk38_rls_ledger_and_inbox.py` -- extends
    `tests/test_row_level_security.py`'s real, non-superuser `app_role`
    unfiltered-query RLS proof to two financially load-bearing models
    that had no such proof yet: `LedgerEntry` (the append-only four-book
    accounting journal) and `InboxEvent` (the integration inbox's
    durable event log). Six new tests covering unfiltered-select,
    read-by-primary-key, and no-scope-means-no-rows for each model, same
    shape as the existing Product/PortfolioVersion/PublicationIntent/
    Sleeve/ContentDocument/CustomerProfile coverage. No bug found; RLS
    held in every case.
- **`GET /system/readiness` -- a real release taxonomy and trading-
  authority qualification gate** (ADR-0010). Two fields this build had
  never computed for real: `release_status`
  (`app/services/release_taxonomy.py`'s `ReleaseStage`, a total 1:1
  rename of the existing `Product.lifecycle_state`/`ReleaseReview`
  ladder onto `RESEARCH_ONLY -> SHADOW -> LIMITED_LIVE ->
  FULLY_RELEASED`) and `trading_authority`
  (`app/services/trading_authority.py`'s `assess_trading_authority`, a
  fail-closed, recomputed-every-call gate over release/rights/incident
  state). The gate is structurally unable to return `qualified=True`
  for an order-routing product today -- `CopyMandateState` and
  `ManagedProgramState` genuinely have no ACTIVE/enrolled-live state in
  this build -- and reports exactly that
  (`missing_input:execution_activation_pipeline`), never a fabricated
  pass. Gated by the same `view_deployment_status` permission (owner/
  publisher_operator) as `/metrics`; an optional `?scope=<product_id>`
  reads one product. 18 new tests across
  `tests/test_release_taxonomy.py`, `tests/test_trading_authority.py`,
  and `tests/test_system_readiness.py`.
- `app/api/dependencies.py`'s `require_tenant_scope`: a FastAPI
  dependency that centralizes `app/db.py`'s `set_tenant_scope()`,
  replacing ~85 individual `set_tenant_scope(session, scope.tenant_id)`
  calls previously made at the top of each dashboard route handler in
  `app/api/dashboard_routes.py` with `scope: TenantScope =
  Depends(require_tenant_scope)` in place of `Depends(get_current_scope)`.
  No public behavior changes -- every route that set tenant scope still
  does, through the exact same `set_config(..., is_local=true)`
  mechanism, just once, during dependency resolution instead of
  imperatively in the handler body. A handful of genuinely different
  call sites are left as explicit, documented exceptions rather than
  forced into this one shape: ~8 routes that call `session.rollback()`
  mid-handler (the rolled-back transaction clears the session-local GUC,
  so they still set it again by hand after the rollback); 3 routes
  (`rights_register_page`, `deployment_status_page`,
  `platform_connection_wizard_page`) that need the caller's role for a
  permission check but run no tenant-scoped query at all; the
  pre-authentication `sign_in_submit`/`verify_email_page` bootstrap flow
  (ADR-0009), which has no `TenantScope` yet by construction; and
  `app/api/relay_routes.py`, which never sets `app.tenant_id` at all --
  it runs on the separate, restricted `relay_role` connection instead.
- `/terms` and `/privacy` routes and templates
  (`app/templates/pu09_terms.html`, `app/templates/pu10_privacy.html`):
  ID-01's signup checkbox has always required accepting "the Terms and
  Privacy Policy", but no such route or content ever existed. Both
  pages are explicit, honestly-labeled PLACEHOLDER content pending real
  legal review (not real Terms of Service/Privacy Policy -- no one on
  this team is positioned to author that), with a few generic,
  uncontroversial structural sections (who this applies to, data
  collected, how to contact us). `id01_auth.html`'s signup checkbox now
  links both terms in place of unlinked plain text.
- Risk-of-loss disclosure on the copy-mandate wizard
  (`app/templates/cu09_copy_wizard.html`): the screen where a customer
  sets `allocation_amount`/`max_trade_risk`/`max_loss` had no risk
  disclosure anywhere on the page. Added a "Risk and fee notice" panel
  near the top of the form, matching the wording/tone of the existing
  disclosures on `pu01_home.html` and `pu05_pricing.html`. This build's
  mandates can only ever reach DRAFT/CANCELLED state (no real activation
  pipeline exists), so no real money moves from this screen today, but
  it is still the natural place for this disclosure.
- Track 29: accepted `signal_platform_contracts` v1.1.0's new, additive,
  optional `SourceIdentity.source_catalog_id` field (signal-copier's
  Track 14 Provider/Source/Connection catalog's own `sources.id`) --
  `app/services/integration_inbox.py`'s `SOURCE_RECEIPT`/`SOURCE_EVENT`
  ingestion already tolerates it (shared pydantic model, extra field
  simply parses through); no new column added to `inbox_events` or the
  ledger yet, since no producer sends a real value for it in this
  deployment today -- see `signal-copier`'s own changelog entry for
  which adapter does.
- `app/services/integration_inbox.py`'s `_apply_projection` now has a
  real `EventType.SOURCE_EVENT` branch. Before this, EVERY `SOURCE_EVENT`
  (including an `ORIGINAL` kind -- the raw inbound event that arrives
  BEFORE its own `SOURCE_RECEIPT` on the same
  `signal-copier:source:<name>` stream in the real webhook ingress path)
  fell into the generic "unimplemented event type" branch and parked at
  sequence 0 forever -- which, since `_next_expected_sequence` is keyed
  strictly off `applied_at`, permanently blocked every later event on
  that SAME stream too, including the `SOURCE_RECEIPT` and any
  `ROUTING_ADMISSION_OUTCOME` that followed it. An `ORIGINAL`
  `SourceEventKind` (signal_platform_contracts's own taxonomy) is now
  applied as a no-op: it advances the sequence and stays durably stored,
  verbatim, in its own `envelope_json` (inspectable provenance/audit
  evidence), but produces NO second ledger entry -- the same economic
  fact is already booked into `Book.SOURCE` by that stream's own
  `SOURCE_RECEIPT`, so a second entry would double-count it. Every OTHER
  kind (`EDIT`/`DELETE`/`REPLY`/`CANCEL`/`CLOSE`/`ADD`/`TARGET_UPDATE`/
  `STOP_UPDATE`) genuinely changes or retracts economic state this build
  has no ledger projection for yet, so it still parks honestly, with its
  own new `unimplemented_source_event_kind:<kind>` reason -- never
  blanket-accepted just because that would also unblock the stream.
  Tests: `test_a_source_event_then_receipt_then_routing_outcome_all_apply_in_order`
  (reproduces the exact live scenario and confirms all three now apply,
  in order) and
  `test_a_source_event_kind_this_inbox_cannot_yet_interpret_still_parks_and_blocks_its_stream`
  (confirms this fix does not silently swallow every kind), both in
  `tests/test_integration_inbox.py`.

#### Fixed
- Track 40 (fuzzing/fault-injection extension): `app/api/relay_routes.py`'s
  `ingest_batch` per-event loop only caught three named exceptions
  (`UnregisteredStreamError`/`EventIntegrityError`/
  `SequenceSlotAlreadyConsumedError`) from `ingest_export_event` -- a
  genuinely malformed event anywhere in a batch (an envelope failing
  `EventEnvelope.model_validate_json`, e.g. JSON nested deep enough to
  trip pydantic-core's own internal recursion guard; a per-event-type
  payload with an extra/wrong-typed field, e.g.
  `ExecutionAppliedPayload`'s `extra="forbid"`; or a string field
  carrying a byte Postgres `text`/`varchar` columns reject outright, an
  embedded NUL byte) raised an UNHANDLED `pydantic.ValidationError` or
  `sqlalchemy.exc.DataError` straight out of the route as a real,
  unhandled 500 -- aborting the ENTIRE batch request, including every
  other, already-committed, perfectly valid event processed earlier in
  the same loop. Two new `except` blocks (same shape as the three
  existing ones: rollback, append a clean per-event result, continue)
  now catch both exception types and report `"status":
  "malformed_envelope"` instead. Found and reproduced by
  `tests/test_c40_relay_ingest_adversarial_payloads.py`'s own
  `test_deeply_nested_payload_field_is_rejected_cleanly_not_500`,
  `test_oversized_single_event_string_is_rejected_cleanly_not_500`, and
  `test_embedded_nul_byte_instrument_id_is_rejected_cleanly_not_500`.

- Track 35: `app/services/integration_inbox.py`'s `_apply_projection`
  `EventType.SOURCE_EVENT` branch now has a real, honest disposition
  for every `SourceEventKind`, not just `ORIGINAL` (Track 30, above).
  Before this, `EDIT`/`DELETE`/`REPLY`/`CANCEL`/`CLOSE`/`ADD`/
  `TARGET_UPDATE`/`STOP_UPDATE` all parked with the generic
  `unimplemented_source_event_kind:<kind>` reason and permanently
  blocked everything after them on the same stream -- and this was a
  LIVE bug, not a theoretical one: signal-copier's Telegram
  (`app/sources/telegram.py`, `telegram_user.py`), Slack
  (`app/sources/slack_user.py`), Twitter (`app/sources/
  twitter_user.py`), and email (`app/sources/email_source.py`)
  adapters already emit `EDIT`/`DELETE`/`REPLY` live today. Now:
  - `ORIGINAL`/`ADD`/`REPLY` apply as a no-op and advance the stream
    (verified against signal-copier's own adapters: each independently
    books its own instruction, if any, through its own `SOURCE_RECEIPT`
    before this row is even exported, so this row is pure, already-
    redundant provenance).
  - `DELETE`/`CANCEL`/`CLOSE` apply as a no-op and advance the stream
    (none of these ever carries bookable instrument/side/quantity
    content, by the taxonomy's own definition).
  - `EDIT` correlates by native provider identity: a new, indexed
    `InboxEvent.source_event_native_key` column (Alembic
    `a7c3f29d1e56`) lets an `EDIT`'s own `source.
    original_source_event_id` resolve back to the real, already-
    applied `SOURCE_EVENT` row it revises. Resolves → applied as a
    no-op (same reasoning as ORIGINAL/ADD/REPLY). Doesn't resolve (no
    reference at all, or no match for this tenant) → parks honestly as
    `edit_without_resolvable_target:<...>`, never silently treated as
    safe provenance just to unblock the stream.
  - `TARGET_UPDATE`/`STOP_UPDATE` always park as
    `source_event_kind_not_ledger_representable:<kind>` --
    `LedgerEntry` has no stop-loss/target column at all, so there is
    nowhere honest to write these even with a perfect correlation; no
    adapter emits either kind today, so this remains a disclosed,
    theoretical gap. See `docs/KNOWN_ISSUES.md` for exactly what is
    (and deliberately is not) covered, including a separate, pre-
    existing `SOURCE_RECEIPT`-branch double-booking concern this track
    flagged but did not fix.
  Tests: 11 new cases in `tests/test_integration_inbox.py` covering
  every kind's apply/park disposition, cross-tenant native-key
  isolation (an `EDIT` must never resolve against a different tenant's
  identically-keyed `ORIGINAL`), and a decoy-original case proving a
  wrong/looser correlation can't look right by accident.

- `/auth/signin`'s sign-in success redirect no longer always sends an
  authenticated user to `/app` (the CUSTOMER-only dashboard) regardless
  of role. `id01_auth.html`'s own sign-in form always submits the
  literal default `return_route=/app` (there is no real deep-link flow
  populating it with anything else yet), so an OWNER or any other
  operator role landed on `/app` and hit an immediate 403 straight after
  a successful login (`/app`'s own `_require_own_customer_overview`
  grants `view_own_customer_overview` to `MembershipRole.CUSTOMER` only).
  `sign_in_submit` (`app/api/dashboard_routes.py`) now redirects a
  non-CUSTOMER membership to `/ops` instead, only when `return_route` is
  still that unmodified default -- an explicit, non-default
  `return_route` a future deep-link flow supplies is still honored
  verbatim. Tests:
  `test_sign_in_as_an_owner_redirects_to_the_ops_landing_page_not_the_customer_app`
  (new) and the existing
  `test_sign_in_with_correct_password_sets_session_cookie` (CUSTOMER
  still redirects to `/app`), both in
  `tests/test_id01_id02_id03_auth_routes.py`.
- Accessibility gaps in the shared layout (`app/templates/_base.html`,
  used by every dashboard/auth screen): added a skip-to-content link
  (`.skip-link` → `#main-content`), wrapped the header's navigation
  link in a real `<nav aria-label="Primary">`, and gave `<main>` an
  explicit `id="main-content" role="main"`. Also added `role="alert"
  aria-live="assertive"` to every template's `.conflict` error banner
  (the shared failed-submission pattern, confirmed in 27 templates --
  `id01_auth.html` among them -- not just one), so a screen reader now
  announces a failed submission instead of staying silent. No
  axe-core/pa11y harness exists in this repo; verified by reading the
  rendered template source and by a new test
  (`test_base_layout_has_skip_link_and_landmark_roles`) asserting these
  attributes appear in a real rendered page.
- Track 32: the Track 28 accessibility scaffolding above only ever
  reached templates that `{% extends "_base.html" %}`. 6 of the app's
  41 templates are standalone `<!doctype html>` documents with their
  own `<head>`/styling that do not extend it --
  `pu01_home.html`, `pu02_catalog.html`, `pu03_portfolio_detail.html`,
  `pu05_pricing.html`, `pu08_help.html`, `id04_eligibility.html` -- so
  none of it ever reached these 6 real public/customer routes (`/`,
  `/portfolios`, `/portfolios/{slug}`, `/pricing`, `/help`,
  `/onboarding/eligibility`). Rather than forcing these onto the shared
  dashboard chrome (which would change their deliberately simpler
  marketing/public layout), added the same skip-to-content link,
  `<nav aria-label="Primary">` landmark, and `id="main-content"
  role="main"` region directly into each page's own existing markup,
  matching `_base.html`'s CSS/behavior exactly. All 6 already had a
  `<header>` region to wrap in the nav landmark, so no page needed the
  sub-requirement skipped. Verified with a new test
  (`test_standalone_public_templates_have_skip_link_and_landmark_roles`)
  asserting the same attributes now appear in each of the 6 real
  rendered pages.
- Portfolio version `version_number` race: a database-level unique
  constraint on `(tenant_id, portfolio_id, version_number)` for
  `portfolio_versions`, plus a catch-and-retry-once around it in
  `create_portfolio_version_draft_from_candidate` -- two concurrent
  draft-creation calls for the same portfolio can no longer both claim
  the same version_number; a persistent conflict now raises a clear
  `PortfolioVersionNumberConflictError` instead of leaking a raw DB
  error.
- Startup guard refusing to boot with `ENVIRONMENT=COMMERCIAL_LIVE`
  while any of `LOCAL_JWT_SECRET`, `RELAY_SIGNING_SECRET`,
  `CATALOG_FIT_SIM_SIGNING_SECRET`, or `STRIPE_WEBHOOK_SECRET` is still
  at its repo-committed placeholder default, naming exactly which
  secret(s) are still unrotated.
- HWM fee calculation (`compute_simple_hwm_fee`) now quantizes to 2
  decimal places (cents, `ROUND_HALF_EVEN`) instead of returning an
  unrounded Decimal with arbitrary precision.
- CU-02 "My portfolios" now shows the real product name instead of the
  raw product UUID in the "My selections" table.

#### Tests
- Track 40: fuzzing and fault-injection coverage extension, following
  signal-copier's own established C30 (Schemathesis)/C32 (fault
  injection) pattern rather than inventing a new approach for this
  repo.
  - `tests/test_c39_schemathesis_api_fuzzing.py` -- the first
    Schemathesis pass for this service: `POST /internal/relay/
    ingest-batch` and `POST /api/v1/billing/webhook/stripe` (external
    ingress, `not_a_server_error` only -- generated examples can't
    forge a valid signature), `GET /health`/`/system/readiness`/
    `/api/v1/me` (full response-schema-conformance with a real signed
    Bearer token), and `POST /auth/signin`/`/auth/signup` (ID-01/
    ID-02/ID-03, `not_a_server_error` only -- both return HTML with no
    declared response schema).
  - `tests/test_c40_relay_ingest_adversarial_payloads.py` -- genuinely
    adversarial payloads against the real relay ingress route: wrong
    content-type, truncated JSON, deeply nested/oversized payloads,
    unicode/embedded-NUL/injection-style strings in text fields
    (confirmed stored verbatim as data via a parameterized write, never
    executed/interpolated), a duplicate `event_id` with a genuinely
    different body (the first HTTP-level test of `EventIntegrityError`
    -- previously only unit-tested at the service layer), and an
    out-of-order sequence. Found and fixed the two `ValidationError`/
    `DataError` 500s documented above. Also documents (in
    `docs/KNOWN_ISSUES.md`, flagged rather than fixed -- see that
    file's new "`POST /internal/relay/ingest-batch` reports 'applied'
    for a genuinely PARKED event" section) that this route's per-event
    `"status": "applied"` label is inaccurate for a genuinely parked
    event, which has a real (if largely benign today) consequence for
    signal-copier's own `relay_worker.py`.
  - `tests/test_c38_db_connection_drop_fault_injection.py` -- the
    highest-value check for this track given this is financial
    accounting software: a real `pg_terminate_backend()` against the
    disposable Postgres cluster's own admin connection kills a
    session's backend mid-transaction, after a real ledger write but
    before `COMMIT`. Confirms Postgres's own rollback guarantee leaves
    NO partial `ledger_entries`/`inbox_events` row, and that the exact
    same envelope redelivers and applies cleanly on a fresh connection
    afterward.
- Track 33: `tests/test_rollback_recovery_rls.py` -- the ~8 rollback-
  recovery routes' (e.g. `create_product_draft`) manual post-`rollback()`
  `set_tenant_scope()` re-call (see this date's own `require_tenant_scope`
  entry above) was previously exercised only through
  `tests/test_dashboard_routes.py`'s superuser-backed `_client`, which
  bypasses RLS entirely and so could not actually prove the re-scope call
  restores real tenant isolation. New tests drive the same duplicate-slug
  rollback path over a real, non-superuser `app_role` session (the
  `tenant_session_factory` fixture `tests/test_require_tenant_scope_
  dependency.py` already established), including one that issues a
  deliberately UNFILTERED query (no `tenant_id` where clause at all)
  immediately after the rollback + re-scope, so nothing but real Postgres
  RLS could make it pass. Result: the pattern is genuinely RLS-safe --
  the manual re-scope does correctly restore tenant isolation for the
  next query in the same request. No code change was needed; this closes
  a test-coverage gap, not a bug.

### 2026-09-29

#### Added
- Revocable JWT/Bearer-token sessions: a real `jti`-keyed denylist,
  fail-closed verification, and owner/customer-facing revoke-all-tokens
  UI (`72efeae`).
- AD-18 evidence manifest export: a real, synchronous filtered JSON
  export of audit events with a content-hash manifest header
  (`f752904`).
- CURRENT+PREVIOUS dual-secret signature verification, enabling
  zero-downtime secret rotation across both apps' signing boundaries
  (`9ddc681`).
- CU-06: real max drawdown and completed-episode win rate, computed
  from the real FIFO-lot equity/episode data (`64d596d`).
- INT-027: real routing/admission/fill outcome encoding per
  `SOURCE_RECEIPT` (`edbe3a8`).

### 2026-09-28

#### Added
- PU-03: the fit simulator's real equity curve, now rendered as a real
  Chart.js line chart (`e95fc35`), building on the public "Try our fit
  simulator" panel (`7592e67`).
- Real session audit wired into AD-16/AD-11; AD-01 incident linking
  confirmed (`76cf4d9`).
- Customer-portal screens CU-04/CU-05/CU-06/CU-11/CU-15 (`fc25d37`).
- AD-06 candidate comparison/draft and AD-21 commercial incidents
  (`956bedb`).
- Real local sign-in/sign-up/verify/recovery system, ID-01/ID-02/ID-03
  (`e303af9`) — email delivery not wired (link shown on page instead).
- INT-001: real single-command installation via Docker Compose,
  spanning both apps plus the in-process relay (`777dee1`).
- INT-033: real-account single-writer enforcement — a durable claim
  above `PublisherWriterClaim` so a direct route and an external alias
  for the same real brokerage account can't both claim write authority
  (`3b8cf40`).
- INT-008/INT-009 bootstrap snapshot/manifest mechanism (`b462a77`).
- Producer-generation binding: reject rollback and reused sequence
  slots, INT-010 (`b330d3d`).
- Real, append-only control-plane audit trail on every existing
  command endpoint, S12 step 7 (`89a32b0`).
- Real FOLLOWER observation ingestion, idempotent, S12 step 6
  (`7c29651`).
- Portfolio Lab source feed: export and project `SOURCE_RECEIPT`, S12
  step 5 (`e89464f`).
- Per-analyst P&L attribution for `Book.PLATFORM`, FIFO-lot method,
  INT-026 (`d846626`).
- Real end-to-end late-fee correction path through the inbox, INT-012
  (`420b0e8`).
- Real ordering and gap detection on the commercial inbox, slice 13
  (`0f14efe`).
- A rendered owner "Trading & Integration Status" page, slice 11
  (`407157a`).
- Real owner trading performance (gross realized P&L) reporting, slice
  10 (`7b6c107`).
- The owner/staff Integration Status report, slice 8 (`0697319`).
- The restricted relay worker connecting `signal-copier` to the
  commercial inbox, slice 5 (`3ca33a7`).
- Commercial inbox for the Signal Platform Integration Correction Pack,
  slice 4 (`5a620a7`).
- `signal_platform_contracts` — the pure cross-service event envelope
  and identity contract, integration slice 1 (`c244dbd`).
- AD-11 real Mandates read view (`712cd08`); PU-06 Methodology, risk
  and legal documents (`893a03c`).
- CU-03 selected portfolio detail, CU-01 customer overview, CU-10
  pause copying/position handoff, CU-09 copy setup and mandate wizard,
  CU-07/CU-08 platform connections and wizard, AD-22 commercial
  deployments and recovery, PU-07 public service status, PU-04
  portfolio comparison, AD-15 managed allocations/NAV/dealing, CU-13
  profile/security/display preferences, CU-12 alert delivery
  preferences, CU-02 my portfolios, AD-10 publication intent/cohort
  detail, AD-05 research run and results, AD-20 workspace
  customization, AD-14 managed-program setup, AD-19 content and
  disclosure publishing, AD-13 pricing/entitlements/billing
  operations, AD-17 integrations/data rights/quotas — the bulk of the
  original CU-/AD-/PU- screen build-out (see `git log --oneline` for
  each individual commit).

#### Fixed
- Two real deployment gaps found by actually running the containers
  (`2c1079a`).
- CI smoke test: read the owner token from a file instead of masked
  logs (`43e1ac0`).
- Genericized `platform_performance` to be book-agnostic (`ec9beba`).
- Parked unsupported `schema_version` events, visibly rather than
  silently, INT-007 (`06b2252`).

#### Changed
- `docker-compose.yml`: load `signal-copier`'s real `.env`, wire
  `SESSION_SECRET`, document remaining commercial secrets (`d6613bb`).
- Added the missing `.env.example` for this app (`76984ab`).
- Closed the INT-019 navigation gap and added the INT-027 source
  coverage report (`a3c1cd1`).
- Re-verified the rights registry and command authority against new
  integration boundaries, INT-021/INT-031 (`77a6fef`).
- An acceptance-case verification pass against the integration pack's
  own 40 cases, slice 12 (`1945c2e`).
- Replaced CU-01/CU-03's blanket `UNSUPPORTED` state with precise
  performance states, slice 9 (`ee89ca6`).
- Integration slice 2: `evidence_class` + nullable fee on the ledger,
  S7 (`e640795`).

### Earlier

The original 13-phase build (rights registry, tenancy/RLS, the
four-book append-only ledger, portfolio research's deterministic half,
publication intent write-path, Collective2/eToro/CopyFactory adapter
request-shape mapping, subscriptions/entitlement/Stripe-webhook
verification, onboarding, PAMM/MAM simulation-only accounting, the
model-gateway permission boundary) — see `ops/COMMERCIAL_RESUME.md`,
`ops/commercial_state.json`, and `docs/history/ENGINEERING_LOG.md` for
the full, honest per-phase account.

[Unreleased]: https://github.com/sujigoudar/linkedin_auto_jobs_applier_with_ai/commits/claude/signal-copier-redesign/signal-portfolio-commercial
