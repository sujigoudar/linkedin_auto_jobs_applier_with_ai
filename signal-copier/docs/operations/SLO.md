# Service level objectives / operational thresholds

This document lists only the reliability targets this codebase actually
enforces in code today. It is deliberately not aspirational — anything
listed here traces to a real config field or a real check in `app/`.

## Writer lease timing (`app/writer_lease.py`, `app/config.py`)

| Parameter | Default | Enforced by |
|---|---|---|
| Writer lease duration | 30 seconds (`WRITER_LEASE_SECONDS`) | How long an acquired/renewed lease is valid before `app/promote_cli.py`'s `promote_writer_lease` would treat it as genuinely expired and allow a different site to take over. |
| Writer lease renewal interval | 10 seconds (`WRITER_LEASE_RENEW_SECONDS`) | `app/main.py`'s `_writer_lease_heartbeat` background task renews on this cadence — meaningfully shorter than the 30s lease duration so a missed renewal or two doesn't let the lease look expired to a promotion attempt elsewhere while the process is genuinely alive. |

These are the only numbers in this codebase that resemble a classic "lease
TTL" SLO. They govern failover eligibility timing, not request latency or
uptime — see `docs/FAILOVER.md` for the full mechanism these numbers back.

## Export outbox backlog ceiling (INT-040)

| Parameter | Default | Enforced by |
|---|---|---|
| `EXPORT_OUTBOX_SIZE_CEILING_BYTES` | 256 MiB (`256 * 1024 * 1024`) | `GET /health`'s `outbox_backlog_ok` field, computed against `SignalStore.export_outbox_backlog()` — a real, live `SUM(LENGTH(envelope_json))` over undelivered `export_events` rows, never an estimate. |

This is an **alerting threshold**, not a hard cap — nothing in this
codebase prunes or truncates undelivered export events when the ceiling is
crossed; it exists so an operator is told, well before an actual
out-of-space condition, that the commercial platform has been unreachable
long enough for the backlog to matter. Deliberately **not** folded into
`GET /health`'s critical `status` gate (alongside `database_ok`/
`price_monitor_ok`/`reconciler_ok`) — an over-ceiling backlog does not by
itself compromise this instance's position protection; it's a slower
storage/export-reliability risk, surfaced honestly (also on the dashboard's
TR-16 Storage row) but never allowed to mask or be masked by whether
positions are actually protected right now.

256 MiB is reasoned, in `app/config.py`'s own comment, against real
envelope size (roughly 1-2 KB per `EXECUTION_APPLIED` envelope in this
codebase's own test fixtures) and plausible signal volume from this
project's own sources (webhook/Telegram/Discord/etc., nowhere near
high-frequency-trading volume) — on the order of 150k-250k undelivered
events, comfortably weeks of a fully-down commercial ingress before
tripping, while staying well under 5% of even a modest 5-10 GB deployment
disk. This is an operator-tunable starting point, not a claim about any
specific deployment's real disk capacity — tune down for a small disk, up
for genuinely high volume.

## Background worker freshness thresholds (`GET /health`)

These are not formally named "SLOs" in the code, but they are real,
enforced freshness checks that gate `GET /health`'s overall `status`:

| Worker | Interval config | Freshness window enforced |
|---|---|---|
| `PriceMonitor` | `PRICE_MONITOR_INTERVAL_SECONDS` (default 15s) | `_fresh()` in `app/main.py`: `now - last_success < max(interval * 3, interval + 30)`. |
| `OrderReconciler` | `RECONCILE_INTERVAL_SECONDS` (default 30s) | Same formula. |
| Writer lease | `WRITER_LEASE_SECONDS` (30s) / `WRITER_LEASE_RENEW_SECONDS` (10s) | `writer_lease_ok` — `True` only if this process's own fencing token is still current. |

`provider_scout_ok` and `equity_snapshotter_ok` use the same freshness
formula against their own intervals but are deliberately **not** folded
into the overall `status` gate — a missed or delayed pass on either
doesn't affect position protection.

## What is deliberately NOT an SLO here

- No request-latency target for any HTTP endpoint.
- No formal uptime/availability percentage target for the service itself.
- No committed recovery-point objective (RPO) or recovery-time objective
  (RTO) number for Litestream-based DR — `docs/FAILOVER.md` states plainly
  that "this module has no visibility into replication freshness at all,"
  and `docs/operations/DR.md`/`docs/operations/BACKUPS.md` document that
  gap honestly rather than inventing a number.
- No rate-limit numbers framed as an SLO — `app/rate_limit.py`'s
  30/minute (ingress) and 20/minute (catalog fit-simulation) limits are
  abuse-defense ceilings, not availability guarantees to legitimate
  traffic (see `docs/security/THREAT_MODEL.md`).

Any reliability number not listed above should be treated as aspirational
at best, and should not be repeated as an established SLO until it traces
to a real enforced check the way every entry above does.
