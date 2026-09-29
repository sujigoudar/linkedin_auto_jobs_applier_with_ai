# ADR-0004: A durable, pre-effect command ledger, not just the `orders` table

Status: Accepted
Date: 2026-09-29

## Context

An external release audit examined this codebase and stated the gap
plainly: "Every entry, close, stop, target, replace, cancel and
flatten should have a pre-effect durable intent, idempotency identity,
account/environment, expected revision, request fingerprint, remote
identifiers, uncertainty state and terminal evidence," and separately:
"A PENDING result without a broker order ID remains an acknowledged
exposure gap... Persist the intent before submission and retain an
uncertainty reservation until independent reconciliation resolves it."

Before `command_ledger`, this codebase's only record of "we tried to
submit an order" was the `orders` table row — written **after** the
broker call returned, via `SignalStore.save_order_result`. If the
process died between deciding to submit and that write landing, there
was no durable trace the command was ever attempted at all. A broker
can accept an order and the process can still crash before anything
records that acceptance — exactly the exposure gap the audit names,
and one that `orders` structurally cannot close by itself, since it
only ever learns about a command after the fact.

## Decision

`command_ledger` (`app/db.py`'s SCHEMA comment; `app/command_ledger.py`
for the real call-site contract) is a durable, pre-effect intent log
for every real financial command this service submits to a broker:
entry, close, stop_change, replace, cancel, and flatten (`target_change`
is a reserved enum value with no writer on this branch — logical
targets fire as `CLOSE` or `STOP_CHANGE` commands instead). A row is
written and **committed before** the broker call it describes is ever
made — the opposite ordering from `orders`. Every real call site
(`app/engine.py`, `app/lifecycle/manager.py`) follows the same
three-step shape:

1. `store.open_command_ledger_entry(...)` — pre-effect, durable,
   committed, keyed by a caller-supplied `idempotency_key` (UNIQUE) so
   a retried/duplicated call with the same key never submits a second
   broker order; a different `request_fingerprint` under a reused key
   raises `CommandFingerprintMismatch` rather than guessing which
   request was "real."
2. Call the broker. On a normal return, classify the `OrderResult`
   (`classify_order_result`, or the matching classifier for a
   cancel/replace/protective-stop call) into
   `(uncertainty_state, remote_identifiers, terminal_evidence)` and
   call `mark_command_ledger_outcome`.
3. On the broker call *raising*, classify with
   `ambiguous_evidence_for_exception` and mark `UNKNOWN_AMBIGUOUS` —
   never let an exception skip the ledger update.

`UncertaintyState` names the honest set of outcomes: `PENDING_SUBMISSION`
(pre-effect, unresolved), `SUBMITTED_UNCONFIRMED` (accepted, pollable
via a real `broker_order_id`), `CONFIRMED`/`REJECTED_CONFIRMED`
(terminal), and `UNKNOWN_AMBIGUOUS` — the critical state the audit
named, for a broker call that raised, timed out, or returned
PENDING/ERROR with no `broker_order_id` to poll. `UNKNOWN_AMBIGUOUS`
is never silently dropped or guessed either way; it can only be
resolved by independent reconciliation, never assumed. A process
killed between the ledger commit and the broker call still leaves a
real, durable `pending_submission` row for a restart to find (see
`SignalStore.list_unresolved_command_ledger_entries`), and
`promote_cli.py` prints unresolved entries as part of the mandatory
`--confirm-reconciled` promotion step (ADR-0003).

## Consequences

- Ordering, not the presence of new columns, is the actual point:
  `created_at` is the pre-effect instant, and the whole mechanism
  exists to make "we tried" durable even when "what happened" is still
  unknown.
- `orders` and `command_ledger` are complementary, not redundant:
  `orders` remains the post-effect record of what a broker call
  actually returned (used for reporting, reconciliation, position
  tracking); `command_ledger` is the pre-effect intent log plus the
  running uncertainty-resolution state machine. Neither replaces the
  other.
- At the time `command_ledger` was introduced, it was not yet
  integrated with `capital_reservations`' own id space — that table
  mints its own reservation-local uuid rather than a command/intent id
  (see `capital_reservations`' own SCHEMA comment, and ADR-0007),
  because `command_ledger` postdated it on this branch. A named
  follow-up, not yet done: have `CapitalAllocator.admit` take and
  store the real command/intent id instead of minting its own, so a
  reservation and its originating command share one identifier
  end-to-end.
- `idempotency_key` uniqueness plus `request_fingerprint` matching is
  what makes retries safe: a legitimate retry replays the existing
  row's tracked state; a genuinely different command reusing an old
  key is a caller bug surfaced as an error, never silently allowed
  through.
