# ADR-0010: A bounded, in-memory duplicate-exit guard for managed lifecycles (TRK-27)

Status: Accepted
Date: 2026-10-01

## Context

An audit of managed-lifecycle exit idempotency found a real gap: the
command-ledger `idempotency_key` built for every `request_exit`-driven
submission (`PositionLifecycleManager._submit_exit_order`) uses a
freshly-generated `exit_signal.id` on every call — explicitly documented
there as "not a value a genuine external retry would reliably reproduce."
`CloseArbiter`'s own `pending_exit` guard only protects against a
*concurrent* duplicate exit while one is already in flight; it is a
no-op once the first exit has fully resolved. `app/engine.py`'s
`_handle_signal` SIG-01 replay guard is keyed on `signal.id`, so it
only catches a duplicate that shares the exact same `signals` row (the
same `channel_id`/`message_id`, or the cross-transport correlation
Track 12 already covers).

None of these catch a genuinely duplicate EXIT for the same real-world
event delivered through a *different* `channel_id`/`message_id` (e.g.
two separate collector instances, or a provider re-sending the same
real-world exit through two notification paths) arriving **after** the
first exit has already fully resolved. Before this change, that
duplicate was processed as a brand-new request: `request_exit`/
`_handle_managed_close` would correctly refuse it with "no open
position to close" (never oversold, since `CloseArbiter`'s own
invariant is independent of this gap) — but that refusal was
indistinguishable, in logs and in the returned `OrderResult`, from a
genuine operator error. There was no durable record identifying it as
a recognized duplicate of a specific prior exit.

## Decision

Add a bounded, in-memory "exit episode" guard to
`PositionLifecycleManager`, scoped narrowly to the one case this audit
can honestly claim high confidence in:

- The instant a managed lifecycle's exit makes it `closed`
  (`_apply_exit_fill`, the single place every exit-closing path in this
  module converges — `request_exit`, `resolve_pending_exit`, and
  `on_stop_filled` all call it), record a `_ClosedExitRecord`: this
  episode's `entry_signal_id` (the SAME stable identity
  `app/engine.py`'s Track 18 ownership gate already uses — see
  `PositionPlan.entry_signal_id`'s own docstring — reused rather than
  inventing a parallel identity concept), `closed_at`, and `reason`.
- When a later `request_exit` call (or `_handle_managed_close`'s own
  earlier-returning mirror of the same check) finds `lifecycle is None
  or lifecycle.closed` for that exact `(account_id, symbol)`, and a
  `_ClosedExitRecord` exists for it within
  `config.MANAGED_EXIT_DUPLICATE_WINDOW_SECONDS` (default 900s, the
  same order of magnitude as `SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS`),
  log it at INFO as a recognized duplicate and return an `OrderResult`
  whose `message` says so explicitly.
- The returned result is still `OrderStatus.REJECTED` — **never** a
  fabricated `FILLED` replay of the original exit's quantity.
  `_handle_managed_close`'s own classification already treats REJECTED
  as "nothing applied, nothing outstanding," the true state here;
  reporting FILLED with the original quantity would double-count that
  same execution in every `orders`-table consumer
  (`app/economics.py`, `app/provider_value.py`, `app/trade_episode.py`)
  — exactly the failure TR-EPISODE-01 already guards against elsewhere
  in this module. The only observable change from the pre-existing
  generic rejection is the message and the INFO-level log record.

### What this explicitly does NOT cover

- **In-memory only.** `_last_closed_exit` does not survive a process
  restart — consistent with this module's own already-documented "no
  startup reconciliation" gap (see `docs/design/POSITION_LIFECYCLE.md`'s
  "Documented gap"). A duplicate arriving across a restart, within the
  window, is not caught by this guard.
- **Does not, and is not meant to, catch a duplicate that arrives after
  a genuine re-entry.** Once a new position has been opened for the
  same `(account_id, symbol)` (a real re-entry), `lifecycle.closed` is
  `False` again, and this guard's check is never reached — the new
  lifecycle is processed completely normally. This is deliberate: there
  is no data available at this layer (no episode id travels inside an
  inbound EXIT signal) to tell a genuinely new exit for the new episode
  apart from a very-late duplicate of the old one once re-entry has
  happened. Rather than guess at a correlation heuristic the audit
  could not independently verify, this ADR scopes the guard to the one
  case it can confidently resolve — "a request against a position
  that's already fully flat" — and documents the remainder as an open
  gap. See `app/lifecycle/manager.py`'s `_ClosedExitRecord` docstring
  for the same scope statement kept next to the code, and
  `tests/test_trk27_managed_exit_duplicate_episode.py` for both the
  positive (duplicate recognized) and negative (a real second exit
  after a real re-entry is NOT suppressed) regression tests.

## Consequences

- No schema change: the record is process-local and ephemeral by
  design, so no migration was needed.
- A future pass that wires `BrokerAdapter.get_broker_position` into a
  genuine startup reconciliation (the module's own long-documented next
  step) would also be the natural point to persist this guard's state,
  closing the restart gap above.
- If this codebase ever needs the broader re-entry-aware case (telling
  a stale duplicate for episode N apart from a legitimate new exit for
  episode N+1 once N+1 is already open), it will need either a real
  episode identifier carried through the inbound signal from the
  provider, or a materially different, independently verified
  correlation heuristic — not an extension of this guard's existing
  logic.
