# Performance Standards

The real, documented performance/concurrency constraints in this
codebase — every bound below is copied from an actual module, with the
comment that justifies the specific number.

## HTTP ingress rate limiting (`app/rate_limit.py`)

Uses `slowapi` (a FastAPI wrapper over the `limits` library) with its
default **in-memory, fixed-window** store, keyed by client IP
(`get_remote_address`):

- `INGRESS_RATE_LIMIT = "30/minute"` — applied independently to each of
  the two unauthenticated-until-checked ingress routes (`POST
  /webhook/{source_name}`, `POST /sms/twilio`). Justification: "each
  request still costs real work (JSON/form parsing, an auth compare, a
  store lookup for idempotency) before it can be rejected as
  invalid/unauthorized, so an unrestrained flood can burn CPU and DB
  connections even when every single request is ultimately refused." This
  is a defensive ceiling against abuse, explicitly **not** a throttle on
  legitimate traffic — "a real alerting platform firing several signals
  across different symbols within the same second stays far under it."
- `CATALOG_FIT_SIM_RATE_LIMIT = "20/minute"` — the signed,
  audience-bound `/catalog/providers/{source}/fit-simulation` route,
  lower than `INGRESS_RATE_LIMIT` because every accepted request here is
  "a real `BacktestEngine` replay, real CPU work," materially more
  expensive than the cheap auth-reject path the webhook/SMS limit exists
  for.

**Deliberately not implemented:** a shared backend (Redis via slowapi's
`storage_uri`) for a multi-process deployment behind a load balancer —
the module docstring states this app runs "one process, one writer" (see
the writer-lease single-active-writer model), so the default in-memory
store is the right scope for how this app actually runs; do not add a
shared backend speculatively without that deployment model changing
first.

## Context-source rate limiting (`app/context/*.py`, `aiolimiter.AsyncLimiter`)

Each optional external context source self-imposes a ceiling, generally
below the vendor's own documented limit, specifically so a bug or a
misconfigured polling loop in this process can't get this service's own
IP blocked:

| Module | Limiter | Vendor's documented ceiling | Note |
|---|---|---|---|
| `app/context/fred.py` | `AsyncLimiter(60, 60)` (60/min) | 120 req/min per API key | self-imposed half of FRED's documented limit |
| `app/context/fx.py` | `AsyncLimiter(10, 60)` (10/min) | none documented (Frankfurter) | a self-imposed courtesy ceiling precisely *because* the vendor documents none |
| `app/context/sec_edgar.py` | `AsyncLimiter(5, 1)` (5/sec) | 10 req/sec (SEC fair-access policy) | self-imposed half of SEC's documented limit |

Each limiter instance is created once at module import time and lives for
the process's lifetime (correct for one continuous production event
loop — see `docs/standards/TESTING.md`'s note on the cross-loop
pytest-asyncio warning this causes in tests, which is deliberately
silenced there as a test-harness artifact, not a real bug). When adding a
new external context source, add its own module-level `AsyncLimiter`
sized the same way: below the vendor's documented ceiling when one
exists, and a conservative explicit courtesy ceiling when it doesn't —
never leave a new external HTTP integration unthrottled.

## Bounded concurrency: `PriceMonitor` (`app/pricing.py`)

`_MAX_CONCURRENT_PRICE_LOOKUPS = 10` — every `get_last_price` poll pass
runs through an `asyncio.Semaphore(_MAX_CONCURRENT_PRICE_LOOKUPS)`, so at
most 10 broker round-trips are in flight at once regardless of how many
open managed-lifecycle positions exist. Justification, straight from the
module: "Bounded so a growing number of tracked positions can't turn into
an unbounded burst of simultaneous requests against a broker/exchange's
rate limits — each position's own call is still independent (one
slow/failing symbol doesn't block another), but at most this many are
ever in flight at once." A fully sequential design was explicitly
rejected in the same docstring: "with N open positions, a fully
sequential pass costs N broker round-trips end to end, which means the
*effective* polling interval for the Nth position grows with the position
count." One position's `get_last_price` exception is caught and logged
per-lookup (`# noqa: BLE001 - one broker's failure must not block the
rest`) rather than failing the whole pass.

`on_price_update()` is still called one result at a time (not
concurrently) once each lookup completes, because it does its own
per-(account, symbol) locking in `app/lifecycle/manager.py` — the
concurrency bound on the I/O side (broker calls) is independent of, and
does not remove the need for, the correctness-side per-key locking on the
update side.

## What this table does NOT cover

There is no documented app-wide connection-pool sizing, no documented
DB-query performance budget, and no load-test harness in this repo as of
this writing — do not invent numbers for those. If you add one, document
it here the same way: the real bound, the real justification, and the
vendor/production constraint it protects against, not a generic
round number.
