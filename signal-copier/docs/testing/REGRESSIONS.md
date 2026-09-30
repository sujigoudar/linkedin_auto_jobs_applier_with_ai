# Regression Catalog

Real bugs found, fixed, and now guarded against by name in this
codebase's own commit history. Each entry: what the bug actually was,
what would have happened in production if it shipped, and which test(s)
now catch it. See `docs/standards/TESTING.md` for the "load-bearing
verification" standard every one of these fixes followed (revert the fix,
watch the new test fail for the right reason, restore, reconfirm green).

## AUD-01 — optimistic PENDING-order accounting

**Commit:** `c88bb66` — "Replace optimistic PENDING-order accounting with
distinct-field quantity model (AUD-01)"

**The bug:** the plain (non-managed-lifecycle) execution path applied the
**full requested quantity** to `positions.net_quantity` for a PENDING
order **before any broker confirmation**, whenever
`result.filled_quantity` was `None`. `app/engine.py`'s own module
docstring documented this as intentional at the time: "Position tracking
itself updates from `OrderResult.filled_quantity` when a broker confirms
FILLED, or optimistically from the requested quantity when a broker only
reports PENDING."

**Real-world impact:** an order later rejected, or only partially filled,
left this repo's own record of live holdings **silently wrong** — showing
a position the account didn't actually have, or the wrong size — until a
later reconciliation pass caught up. Any code reading `net_quantity` in
that window (capital exposure checks, a close-quantity decision, a
dashboard) would act on an inflated, unconfirmed number.

**The fix:** every order/fill event now tracks five distinct, named
quantities (`requested_quantity`, `confirmed_cumulative_fill`,
`applied_execution_delta`, `outstanding_possible_fill`,
`actual_remaining_ownership`); `positions.net_quantity` is updated
**only** from a broker-confirmed fill. The full requested quantity for an
unconfirmed PENDING order is tracked *separately* as genuine uncertain
exposure via `outstanding_possible_fill`, exposed through
`SignalStore.get_outstanding_possible_fill` /
`PositionLifecycleManager.get_outstanding_possible_fill` rather than
guessed into the confirmed position.

**Tests that catch it:** `tests/test_aud01_distinct_quantity_model.py`
— `test_partial_fill_leaves_actual_remaining_ownership_at_confirmed_not_requested`,
`test_pending_order_later_rejected_leaves_zero_position_impact_and_clears_outstanding`,
`test_pending_order_later_fully_filled_updates_all_fields_correctly`,
`test_rapid_double_reconciliation_pass_does_not_double_count`. Two
pre-existing suites (`tests/test_pending_fill_reconciliation_integration.py`,
`tests/test_edd2b70_review_regressions.py`) previously asserted the *old*
optimistic behavior by name (`# optimistic`) and were corrected — the
commit states they were "verified to genuinely fail against the reverted
optimistic code first."

## P0-9 — IBKR / Rithmic zero-vs-`None` truthiness coercion

**Commit:** `d363e79` — "Fail-closed sweep (P0-9, broader scope): fix
silent zero->unknown coercion in broker/source fill data"

**The bug:** two independent call sites used a truthiness idiom
(`x or None` / `x if x else None`) to normalize an "absent" value —
which silently turns a genuine `0.0` into `None`:

- `app/brokers/ibkr.py::get_order_status` — `filled_quantity`/
  `filled_price` used `x or None`.
- `app/sources/rithmic.py` — the fill-notification handler used
  `fill_price or avg_fill_price or None` and `fill_size if fill_size else
  None`.

**Real-world impact:** downstream, `app/engine.py` and
`app/lifecycle/manager.py` treat a `None` fill as "unknown, fall back to
the full requested quantity" — so a real, broker-reported **zero-share
fill on a `status == "Filled"` order** (a genuine broker-glitch edge
case) would have been misreported as a **full fill**. Worse on the
Rithmic side: `signal.price` feeds
`app/engine.py::_try_reserve_capital`, which **skips the account's
notional-exposure ceiling check entirely** whenever `order_signal.price
is None` — so a fill price that collapsed to `None` wasn't just a
display bug, it was a **silent admission-check bypass** that could let a
position through with no exposure ceiling enforced at all.

**The fix:** both call sites switched to explicit `is not None` checks,
so a real `0.0` is preserved and only a genuinely-missing value falls
back to `None`. See `docs/standards/CODING.md` §2 for the "never coerce a
financial quantity with `or`/truthiness" rule this established.

**Tests that catch it:**
`tests/test_ibkr_broker.py::test_get_order_status_filled_with_a_genuine_zero_fill_stays_zero`;
`tests/test_rithmic_source.py::test_genuine_zero_fill_price_is_not_silently_discarded`,
`test_genuine_zero_fill_size_is_not_silently_discarded`, and
`test_missing_fill_price_falls_back_to_avg_fill_price` (confirming the
*legitimate* fallback still works for a genuinely-missing value). The
commit's own load-bearing verification: "reverted the ibkr.py fix alone,
confirmed [the test] fails with `assert None == 0.0` (the exact old
bug), then restored the fix and reconfirmed green."

## P0-3 — capital allocator: price-absent admission skip, and unresolved exposure treated as zero

**Commit:** `c6e4e7b` — "Capital allocator: fail-closed sizing,
unresolved-exposure block, owner-wide + risk-basis gates (P0-3)"

**Bug 1 — price-absent admission skip:** a signal with no resolvable
price used to **silently skip the entire admission check** whenever a
gate (`max_notional_exposure`, `risk_percent_of_equity`) was configured —
i.e. an unbounded admission slipped straight past every configured
exposure/risk ceiling.

**Bug 2 — unresolved exposure counted as zero:** a position whose average
cost couldn't be resolved used to contribute `0.0` to an account's total
exposure — meaning a position the system genuinely couldn't value was
treated as if it carried **no risk at all** when summing exposure against
a ceiling.

**Real-world impact:** either bug independently meant the account's real,
configured notional/risk ceiling could be silently exceeded — the exact
kind of gap a capital-allocation safety gate exists to prevent. Bug 2 is
the more insidious of the two: a position genuinely can't be valued
(broker gave no readable cost basis), and the system's response was to
treat that unknown as "zero risk," the opposite of fail-closed.

**The fix:** a signal is now **rejected** (fail closed) whenever any gate
is configured and the signal carries no resolvable price. Position
valuation now returns an `ExposureReport` carrying `unresolved_symbols`
explicitly, and every admission gate **rejects outright** when that list
is non-empty, instead of silently summing it as zero.

**Tests that catch it:** `tests/test_e03_capital_exposure_gate.py` —
`test_no_price_on_the_signal_is_rejected_when_a_gate_is_configured` (Bug
1), `test_unresolved_exposure_blocks_new_admissions_rather_than_counting_as_zero`
and `test_resolved_exposure_on_other_accounts_is_unaffected_by_one_accounts_unresolved_symbol`
(Bug 2), plus the broader ceiling/contention suite in the same file
(owner-wide ceiling, risk-basis sizing) and
`tests/test_capital_allocator.py`. Commit's own load-bearing
verification: "reverted each of the two named bugs' fixes in turn,
confirmed the corresponding new test genuinely fails for the right
reason, then restored and reconfirmed green."

## P0-6 — `WriterLeaseGuard.acquire()` thread-safety race

**Commit:** `55976eb` — "P0-6: cross-process fencing + manual-only
failover for the writer lease" (the guard's own `self._lock` and its
justifying comment ship as part of this same commit, in
`app/writer_lease.py`).

**The bug:** `WriterLeaseGuard` is a singleton constructed once at
`app.main` import time. `acquire()`'s "idempotent repeated call" fast
path checked `self._token is not None` and, if so, skipped a **real**
acquisition. Without a lock around the check-then-act sequence (read
`_token`, possibly call the store, write `_token`), **two OS threads**
could both observe `_token is None` simultaneously, both perform a real
acquisition/renewal against the store (each bumping the DB's fencing
token), and then race to write `self._token` — whichever write landed
last won, and it could be the **stale, lower** token value.

**Real-world impact:** a process could end up fencing **itself** out —
`require_active()` on the very next command-execution call would see the
in-memory `_token` (stale, from the losing write) mismatch the store's
actual current token (from the winning, later acquisition this exact same
process performed) and raise `FencedOutError`, refusing to submit any
further commands. This isn't a real second-writer condition at all — it's
a process that legitimately holds the lease incorrectly believing it's
been superseded, purely because of an internal race in its own guard
object. In production this race window is specific to FastAPI's
`lifespan()` running on anyio's thread-based portal combined with this
project's own test suite re-entering `TestClient`/`lifespan` many times
against the one process-wide singleton (see `docs/testing/FIXTURES.md`
for the full mechanism) — genuinely rare in a real single-`lifespan()`
deployment, but real and reproducible under this suite's own test
concurrency pattern, which is precisely how it was found.

**The fix:** `self._lock = threading.Lock()` around the full
check-then-act sequence in both `acquire()` and `renew()` — a plain
`threading.Lock`, not `asyncio.Lock` (which only excludes coroutines on
one event loop, not the separate OS threads FastAPI's `lifespan()`
actually uses here).

**Tests that catch it:**
`tests/test_writer_lease_fencing.py::test_concurrent_acquire_from_multiple_threads_is_race_free`
— 8 real OS threads synchronized on a `threading.Barrier` to maximize
simultaneity, with the race window between the guard's `_token is None`
check and the store call actually landing artificially widened, asserting
every thread ends up agreeing on the same final token with no thread
left un-finished (deadlock check) and no thread fenced out by its own
process. The broader `tests/test_writer_lease_fencing.py` suite (13
tests) covers the surrounding fencing/promotion contract this fix sits
inside: `test_first_acquire_claims_token_one`,
`test_different_site_cannot_acquire_automatically_even_when_expired`,
`test_second_promotion_fences_out_the_first_guard_immediately`,
`test_renew_after_fencing_raises`,
`test_fenced_engine_refuses_to_execute_further_commands`.

## How to add a new entry here

When a real bug is found and fixed (not a style/refactor change), add an
entry in this same shape: what the bug was (with the exact old code
idiom if there is one), what it would have done in a real account/trade
scenario, and the exact test name(s) that now fail if the fix is
reverted. If the fix didn't go through the load-bearing verification
standard (`docs/standards/TESTING.md`), do that first — an entry here
without a test proven to fail for the right reason isn't a real
regression guard.
