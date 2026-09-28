# Signal Platform Integration Correction Pack — acceptance case status

Tracks the 40 supplemental cases in the pack's own
`INTEGRATION_ACCEPTANCE_CASES.json` against what slices 1–11 of this
integration actually built (see the `claude/signal-copier-readiness-sm44tr`
branch's own commit history for each slice). This is a status report, not
a second copy of the cases — read it alongside that file.

Every `PASS` below cites the real test(s) that exercise it; every `PARTIAL`
or `BLOCKED` says exactly what's missing. Nothing here is asserted without
either a cited automated test or an explicit architectural reason (e.g. "no
code path exists that could do the prohibited thing"). Cases needing
infrastructure this environment doesn't have (a real broker sandbox, disk-
pressure simulation, multi-process outage injection) are marked `BLOCKED`
with what would be needed, never silently skipped or claimed done.

Last updated: after slice 13 (ordering/gap detection, INT-006), on top of
slice 11 (`407157a`), the `signal_platform_contracts` import-boundary
tests (INT-035) and the INT-038 XSS-inertness test (both slice 12).

## Legend

- **PASS** — real, automated, currently-passing test evidence exists.
- **PARTIAL** — the safe/honest half is real and tested; the rest is
  either out of this integration's built scope or needs a piece not yet
  built (named explicitly).
- **BLOCKED** — needs real infrastructure/credentials this environment
  doesn't have (a live broker/publisher sandbox, multi-process fault
  injection, deployment-level storage ceilings). Not attempted with fakes.
- **NOT_RUN** — genuinely not addressed by any slice yet; typically because
  it depends on a subsystem this integration explicitly deferred (Portfolio
  Lab's source-history feed, the real FOLLOWER observation connector, the
  control plane).

| Case | Title | Status | Evidence / gap |
|---|---|---|---|
| INT-001 | Empty integrated installation | PARTIAL | Both apps' own empty states are real and tested (`test_integration_status.py::test_a_tenant_with_no_registered_streams_reports_an_empty_report`, `test_trading_performance_page.py::test_trading_page_shows_the_real_empty_state`, `test_customer_overview.py::test_overview_is_empty_before_any_selection`). No single script starts both apps + relay together as one installation — that's deployment orchestration, not built. |
| INT-002 | Committed fill exported once | PASS | `test_export_events_wiring.py::test_a_real_fill_produces_a_real_undelivered_export_event`, `test_integration_inbox.py::test_ingest_execution_applied_event_creates_a_real_platform_ledger_entry` — real engine fill → real outbox row → real PLATFORM ledger entry, exactly 10 units, SYNTHETIC/paper evidence_class attached throughout. |
| INT-003 | Source transaction rollback | PASS | `test_export_outbox.py::test_a_failed_order_insert_leaves_no_orphaned_export_event` — the exact atomicity property, load-bearing verified in slice 3. |
| INT-004 | Receiver commits then response lost | PASS | `test_integration_inbox.py::test_reingesting_the_identical_event_is_a_harmless_no_op`, `test_relay_worker.py::test_run_once_signs_the_batch_and_marks_applied_events_delivered` — retry-safe by construction (idempotent ingest + only-delivered events marked). |
| INT-005 | Identical identity with conflicting payload | PASS | `test_integration_inbox.py::test_reingesting_the_same_event_id_with_a_different_payload_raises` — `EventIntegrityError`, load-bearing verified in slice 4. |
| INT-006 | Ordering and gap detection | PASS | `test_integration_inbox_ordering.py::test_delivering_the_missing_predecessor_cascades_through_the_parked_successor` — delivering sequence 1 before 0 parks it unapplied (`test_an_out_of_order_arrival_is_received_but_not_applied`); delivering 0 afterward applies both, in order, without redelivering 1. Load-bearing verified in slice 13: removed the cascade loop and confirmed exactly the two cascade-dependent tests failed. |
| INT-007 | Unsupported schema version | PARTIAL | An `EventType` this build doesn't implement a payload for is stored but never applied (`ingest_export_event`'s own trailing comment). A literally malformed/future `schema_version` is rejected by pydantic validation (422) rather than parked-and-visible on the stream status — not the same behavior the case asks for. |
| INT-008 / INT-009 | Snapshot/bootstrap cases | NOT_RUN | No snapshot/bootstrap mechanism exists (`INTEGRATION_DECISION.md` S6 "Snapshot plus deltas" — explicitly deferred, every event is live-delivered only). |
| INT-010 | Producer restored to older DB | NOT_RUN | No producer-generation binding exists. |
| INT-011 | Correction replaces rather than adds | PASS | `test_ledger.py::test_a_correction_adds_a_new_row_and_leaves_the_original_untouched` (pre-existing, Phase 03) — the original row is never edited, a correction is a new row referencing it. |
| INT-012 | Late fee revises net report | PARTIAL | The building blocks are real and tested (`fee: Decimal | None`, `unknown_fee_entry_count` in `platform_performance.py`, `append_correction` inheriting evidence_class) but no single test exercises "gross now, ingest a late fee correction, net becomes available, duplicate fee correction doesn't double-count" end to end through the relay. |
| INT-013 | Paper origin never becomes live | PASS | `evidence_class` is set once at ingest from the envelope and never reassigned anywhere in this codebase (no relabeling code path exists to test the absence of). |
| INT-014 | Publisher ack ≠ follower execution | PASS | `test_customer_performance_state.py`'s own `AWAITING_OBSERVATIONS` tests — a connected, mandated (i.e. "subscribed") customer with zero real `Book.FOLLOWER` entries reports exactly that, never a fabricated holding. |
| INT-015 | Different model and follower nets | PASS | `compute_book_performance(session, tenant_id=..., book=...)` (slice 14) is the real, book-agnostic replay; `compute_platform_performance` is now a thin wrapper over it fixed to `Book.PLATFORM`. `tests/test_platform_performance.py::test_model_and_follower_nets_for_the_same_instrument_never_mix` builds MODEL (net 100) and FOLLOWER (net 80) fills on the same instrument and asserts each book's own report shows only its own net and only its own closing-fill count. Load-bearing verified: temporarily dropping the `LedgerEntry.book == book` filter made exactly this test and `test_only_platform_book_entries_are_counted_not_other_books` fail (both books' P&L summed together), confirming the filter is what the test actually exercises; restored and re-verified green. |
| INT-016 | Cross-customer data isolation | PASS | `test_row_level_security.py`, `test_relay_role_access.py`, `test_customer_performance_state.py::test_an_observation_for_another_tenant_never_leaks_in`, `test_platform_performance.py::test_a_different_tenants_entries_never_leak_into_this_tenants_report` — real Postgres RLS plus explicit service-layer scoping, both tested. |
| INT-017 | Telemetry cannot self-assign ownership | PASS | `test_integration_inbox.py::test_ingest_execution_applied_event_is_refused_for_an_unregistered_stream` — the envelope carries no tenant_id at all; only server-side `register_export_stream` rows bind a stream to a tenant. |
| INT-018 | Staff role is not private trading authority | PASS (by construction) | The commercial app has zero code paths that call signal-copier's private close/flatten endpoints — the two apps share no execution-authority code, only the one-way outbox→relay→inbox flow. |
| INT-019 | Permitted owner workspace switch | PARTIAL | `/ops` → `/ops/trading` navigation is real and tested; Portfolio Lab isn't yet linked from the same nav (it predates this integration and wasn't touched). No shared-cookie risk exists since the two apps have entirely separate session stacks by construction. |
| INT-020 | No connection means no follower result | PASS | `test_customer_performance_state.py`, `test_dashboard_routes.py::test_customer_overview_page_shows_the_real_row_after_a_full_selection_connection_mandate_chain` (asserts `AWAITING_OBSERVATIONS` literally in the rendered page). |
| INT-021 | Current rights prohibit redistribution | NOT_RUN | Rights registry exists (Phase 01) but wasn't re-verified against this integration's own new export/inbox/report boundaries in this session. |
| INT-022 | Private positions excluded from public catalog | PASS (pre-existing) | The public catalog route only ever reads `PUBLISHED` product artifacts (Phase 05/09); no route exposes raw ledger rows publicly — unchanged by this integration. |
| INT-023 | Commercial service outage | PASS (by construction) | `app/relay_scheduler.py`'s own loop swallows every exception and retries next poll (`except Exception: logger.exception(...)`) without touching `engine.handle_signal` at all — the engine has no awareness the relay or commercial app exist. Not exercised under an actual multi-process outage in this session, but the decoupling is structural, not best-effort. |
| INT-024 | Reporting outage does not become market data | PASS (pre-existing) | `PriceMonitor`/`get_last_price` (signal-copier) never reads from the commercial dashboard — unrelated code paths, unchanged by this integration. |
| INT-025 | Missing marks and fees survive import | PASS | `platform_performance.py`'s `unknown_fee_entry_count`, `ad_trading_performance.html`'s own explicit "gross only" labeling — tested in `test_platform_performance.py`. |
| INT-026 | Analyst allocation survives shared symbol | PARTIAL | signal-copier's own `provider_value.py` already does per-(source, analyst) attribution (pre-existing); the commercial side's `Book.PLATFORM` replay has no analyst-level dimension yet. |
| INT-027 | All permitted source outcomes reach research | NOT_RUN | No `SOURCE_RECEIPT` events are ever exported yet (only `EXECUTION_APPLIED`) — this is exactly S12 step 5's "Feed Portfolio Lab from qualified source and performance histories," explicitly not yet built. |
| INT-028 | Single product publication mode | PASS (pre-existing) | `publication_admission.py` (Phase 05) enforces an explicit mode per product; unchanged by this integration. |
| INT-029 | Late subscriber does not replay old entries | PASS (pre-existing) | CU-09's own mandate wizard defaults to `new_entries_only` (Phase 05); unchanged by this integration. |
| INT-030 | Preview cannot create effects | PASS (by construction) | Every route this integration added is a `GET` computing a read-only report (`/api/v1/ops/integration-status`, `/api/v1/ops/platform-performance`, `/ops/trading`) — none has a side effect. |
| INT-031 | Command authority rechecked on execution | NOT_RUN | No new command endpoints were added by this integration; not re-verified against pre-existing ones in this session. |
| INT-032 | Billing event is not a close command | PASS (by construction) | No code path connects billing state to signal-copier's private endpoints — structurally impossible given the apps share no write access. |
| INT-033 | Shared account cannot gain a second writer | NOT_RUN | No canonical-account-identity dedup across a direct adapter and an external platform alias exists. |
| INT-034 | Cross-tenant pooled connection reuse | PASS | `test_relay_role_access.py`'s own real-`relay_role` RLS tests, `integration_inbox.py`'s own `set_tenant_scope(..., is_local=true)` docstring/design (slice 5) — load-bearing verified. |
| INT-035 | Contract package cannot import financial runtime | PASS | `signal_platform_contracts/tests/test_import_boundary.py` (added this pass) — a static source scan AND a real subprocess import with `sqlalchemy`/`psycopg`/`httpx`/`app`/`alembic` blocked, both load-bearing verified by injecting a real forbidden import and confirming both tests catch it. |
| INT-036 | Research job cannot grant live authority | PASS (pre-existing) | `portfolio_research.py` is pure combinatorics with no publish/release call in it at all. |
| INT-037 | Full integration vertical slice | PARTIAL | Every leg up through "staff source/performance available with correct labels" has real, separately-tested coverage (slices 4–11). The final leg — "customer result appears only after its own observation" — is real in design (`AVAILABLE` is a genuine, reachable state) but not exercisable end-to-end today because no real FOLLOWER-observation connector exists yet (S12 step 6). |
| INT-038 | HTML and error text remain inert across bridge | PASS | `test_trading_performance_page.py::test_a_malicious_source_stream_or_instrument_name_renders_inert` (added this pass) — a `<script>` marker in `source_stream`/`instrument` renders escaped, not live, on the new Trading & Integration Status page. Not re-verified across every pre-existing screen in this pass. |
| INT-039 | Same financial definition agrees across views | PASS (trivially) | `platform_performance.py` is the only code path that computes this metric; no second, potentially-conflicting definition exists yet to disagree with it. |
| INT-040 | Local disk pressure cannot discard financial evidence | BLOCKED | Needs a real storage-ceiling/alerting policy for signal-copier's own SQLite outbox under actual disk-pressure simulation — deployment/ops work, not attempted with a fake filesystem. |

## Summary

- **PASS: 25** — each row above cites its own specific evidence (a
  slice-4-through-13 test, a pre-existing test from an earlier phase, or a
  named structural/architectural reason with no counter-example code path)
- **PARTIAL: 7** — real, honest partial coverage; each row names exactly
  what's missing
- **BLOCKED: 1** — needs real deployment infrastructure
- **NOT_RUN: 7** — depends on explicitly-deferred subsystems (source
  ingestion, snapshot/bootstrap, generation binding, account dedup) or
  wasn't re-verified in this pass

No case above is marked PASS without a cited, currently-passing automated
test or a stated structural reason with no counter-example code path. Where
a case is genuinely unaddressed, it says so rather than being silently
omitted from this table.
