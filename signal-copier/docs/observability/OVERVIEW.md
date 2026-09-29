# Observability overview

## Metrics: `GET /metrics` (Prometheus, owner-gated)

`app/metrics.py` (C23/E10) exposes private aggregate operational metrics in
Prometheus text format at `GET /metrics`, behind owner auth
(`require_owner_read` — a valid session required, no CSRF since nothing is
mutated) — **never** a public endpoint, unlike `GET /health`.

Deliberate scope constraints:

- **Bounded labels only.** Never a broker order id, a signal id, or any
  secret — nothing here can grow an unbounded-cardinality series from a
  growing number of distinct symbols/orders.
- **Describes actual work done, not "the loop iterated."** The same
  distinction `GET /health` draws: a reconciliation pass that ran but
  corrected nothing still needs its own age tracked, but a feed that's
  silently stopped updating must show up as stale, not a healthy zero.
- **Read-only.** This module only ever reads existing in-memory/store
  state, never mutates anything — a monitoring/metrics outage must never
  grant trading authority or cause a duplicate writer.

### Exposed gauges

| Metric | Meaning |
|---|---|
| `signal_copier_price_observation_age_seconds` | Seconds since `PriceMonitor`'s last pass with at least one usable price read. Absent (no sample emitted at all) if no successful pass has ever completed — deliberately not a phantom `0.0`, which would falsely read as "just succeeded." |
| `signal_copier_reconciler_cycle_age_seconds` | Seconds since `OrderReconciler`'s last completed pass. Same absent-if-never-succeeded convention. |
| `signal_copier_pending_entries` | Managed-lifecycle entries with an unresolved broker outcome. |
| `signal_copier_pending_exits` | Managed-lifecycle exits with an unresolved broker outcome. |
| `signal_copier_protection_deficit_positions` | Open managed-lifecycle positions with owned quantity > 0 whose protective stop is not confirmed standing (unprotected, or a resize/placement still in flight). |
| `signal_copier_open_positions` | Non-flat tracked positions across all accounts — this service's own record, not a live broker read. |

## Structured logging: structlog (C22)

`app/logging_config.py` configures `structlog` for the core financial
path's correlated, structured logging — JSON output by default
(`structlog.processors.JSONRenderer()`), a human-readable console renderer
available for local development. Configured processors:
`structlog.contextvars.merge_contextvars`, `add_log_level`,
`TimeStamper(fmt="iso")`. Uses `structlog.PrintLoggerFactory()` (not stdlib
logging reformatted) and a filtering bound logger with no level filtering
applied at this layer.

Only code that explicitly calls `structlog.get_logger(...)` participates —
this is a deliberate, targeted adoption for the core financial path, not a
blanket replacement of every `logging.getLogger(__name__)` call across the
codebase (`app/auth.py`, `app/writer_lease.py`, etc. still use stdlib
`logging`). A context manager (`bind_contextvars`/`reset_contextvars`) lets
a `with` block bind fields (e.g. an account id, a signal id) onto every
structlog call made anywhere during that block, for request-scoped
correlation.

## Health and readiness endpoints

### `GET /health` — public, minimal, unauthenticated

Deliberately public and minimal: liveness (this response happened at all)
plus whether the background workers that keep positions protected are
making progress. No account IDs, balances, or other private data belongs
here. `*_ok` fields are `False` both when a worker hasn't completed a pass
recently (stuck/dead task) and before its very first pass after startup —
never defaulting to a false "ok."

Response fields: `status` (`"ok"`/`"degraded"` — only `"ok"` if
`database_ok` and `price_monitor_ok` and `reconciler_ok` and
`writer_lease_ok is not False`), `database_ok`, `price_monitor_ok`,
`reconciler_ok`, `provider_scout_ok` (informational, not gating `status`),
`equity_snapshotter_ok` (informational), `relay_ok` (`None` if the relay
isn't configured at all — never falsely "not fresh" for a feature that was
never meant to run), `outbox_backlog_ok`/`outbox_backlog_bytes`/
`outbox_backlog_row_count`/`outbox_backlog_ceiling_bytes` (INT-040, also
informational — a storage-reliability risk, not a position-protection
one), and `writer_lease_ok` (`None` on a standby, which never holds a
lease; `True`/`False` on an active writer — see `docs/FAILOVER.md`).

### `GET /system/readiness` — owner-gated, the 6-dimension model

`GET /system/readiness` (owner-gated, `require_owner_read`) answers a
richer question than `GET /health`'s "is this process/worker responding":
whether this instance is genuinely *ready to trade right now*, across six
independent dimensions:

1. **`liveness`** — is this process/its database probe responding at all.
2. **`data_readiness`** — per configured account, was a LIVE broker
   balance read successfully and recently — deliberately distinct from
   liveness: the service can be fully reachable while this is `unknown` or
   `partial` if a broker's balance-readback capability isn't verified or
   hasn't succeeded recently.
3. **`market_data_readiness`** — `PriceMonitor`'s own real freshness
   (the same underlying signal `GET /health`'s `price_monitor_ok` uses),
   folded into a single boolean here alongside the other dimensions.
4. **`trading_authority`** — whether this process currently holds a valid
   writer-lease fencing token (placeholder pending fuller P0-6
   fencing/lease integration into this endpoint specifically).
5. **`protection_readiness`** — for open managed-lifecycle positions, is
   every one confirmed protected (a standing stop), surfaced as a
   `stop_gap_count` when not.
6. **`release_status`** — the qualification/release-approval state for
   this deployment (placeholder pending fuller P0-7
   qualification/release-taxonomy integration).

`_compute_readiness_rollup` folds all six into a single label (`ACTIVE`/
`DEGRADED`/`NOT READY`) with a human-readable `reason` — e.g. `NOT READY`
if `GET /health` itself was unreachable this cycle ("nothing below can be
verified live"), or if `trading_authority` reports `not_held`. This rollup
is a convenience summary, not a replacement for inspecting the individual
dimensions — the underlying six always ship in the response body alongside
it.

## What is deliberately not built

- No distributed tracing.
- No APM/error-tracking integration.
- No log shipping/aggregation configuration in this codebase — structured
  JSON output is the extent of it; forwarding it anywhere (Loki,
  CloudWatch, etc.) is left to the deployment environment.
