# Allocation scenario traceability

Machine-readable registry: [ALLOCATION_TRACEABILITY.yaml](ALLOCATION_TRACEABILITY.yaml)
(213 rows over groups A-R of the scenario list). `tests/test_alloc10_traceability.py`
fails if a row names a test function that does not exist, so this cannot rot silently.

Each row: scenario, initial state, event sequence, expected invariant, `status`
(covered / partial / unit-only / gap), `impl_status` (does `app/` implement the
behavior at all), the verified test references, and what the test actually asserts.

**Environment for every "covered" row: isolated tests against the paper broker and
SQLite. No row is broker-qualified, and none is live-released.**

## Counts

| status | rows |
|---|---|
| covered | 114 |
| partial | 21 |
| unit-only | 17 |
| gap | 61 |

Implementation: 143 implemented, 32 partial, 38 missing.

## Most serious gaps (implementation missing, not just untested)

Rows were written from a read-only review of the code. Items marked **verified**
were re-checked directly against the code afterwards; the rest are as reported by
that review and have not been independently reproduced.

1. **Daily-loss and min-equity gates are not functional (verified).**
   `app/daily_loss_limiter.py` reads `SignalStore.get_daily_pnl` and
   `SignalStore._broker_adapters`, neither of which exists. The existing tests mock
   those attributes, which hid it. With a real store, a configured limit used to
   raise out of signal handling; it now fails closed (rejects every entry for that
   account, `tests/test_alloc09_loss_limit_fails_closed.py`). There is still no real
   daily-P&L source, loss latch, or trading calendar (rows N-02, N-03, N-05).
2. **Edited entries can place a second live entry (B-11).** A new revision id is a new
   signal; a test pins two orders.
3. **Lost-response entry on a plain account is never reconciled (I-01, J-05).** The
   hold added by ALLOC-05 keeps the capital reserved, but nothing reads the broker to
   adopt the real holding.
4. **Options are not executable (L-03 to L-06).** Tastytrade declares OPTION support
   but sends equity legs; no naked-short or 0DTE guard exists in `app/`.
5. **Tastytrade always sends "to Open" (J-12, verified in the adapter docstring):** a
   closing sell opens a short.
6. **Limit and stop entry types are parsed but brokers send market orders (H-10).**
7. **CLOSE routes by current rules, not by ownership (C-13):** removing a rule strands
   its position.
8. **No stop-update, partial-trim or cancel-entry handling (B-13, B-15, B-16).**
9. **Same-direction multi-provider ownership (DL-09/EXE-09):** a managed lifecycle
   rejects any second entry on the same (account, symbol); no immutable allocation id.
10. **Close/stop commands still treat an existing `PENDING_SUBMISSION` ledger row as
    new** (only the plain entry path was hardened in ALLOC-05).

## Why the broadcast and the loss-limiter defects survived

Both were "implemented and tested" in isolation: the fan-out was documented and
asserted as intended behavior, and the loss limiter was tested only against a
mocked store. Neither was exercised through the real ingestion path with a real
store. The ALLOC regressions and the browser/API tests above close that for the
allocation path; the loss limiter remains open (item 1).
