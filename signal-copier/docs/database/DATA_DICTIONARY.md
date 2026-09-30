# Data Dictionary: field-level semantics for the trickiest columns

This document does not repeat `docs/database/SCHEMA.md`'s column
listings. It exists for the columns whose *meaning* is not obvious
from a type and a one-line comment — the WHY and the exact contract a
caller must honor, drawn directly from `app/db.py`'s own inline
comments and the modules that write these columns.

## `orders`: the distinct-quantity model (AUD-01)

Four columns together replace an older "optimistically apply the
requested quantity to positions while an async broker's order is still
PENDING" behavior. See ADR-0005 for the decision; this section is the
exact per-column contract.

- **`requested_quantity`** — what was asked for. Always populated when
  known.
- **`confirmed_cumulative_fill`** — the broker's own reported
  cumulative filled quantity for this order, exactly as given
  (`OrderResult.filled_quantity`). **Never** a fallback to
  `requested_quantity`. `NULL` means the broker has not confirmed
  anything yet for this specific PENDING order — not zero fill, but
  genuinely unknown-so-far.
- **`applied_execution_delta`** — the actual signed-by-side quantity
  this exact save applied to `positions.net_quantity` via
  `record_fill`, if anything.
  - `0.0` (not `NULL`) is a genuine, known fact: "this save confirmed
    nothing new and touched the position not at all" (e.g. a
    still-PENDING order with no confirmed fill yet).
  - `NULL` means this row predates the field, or the call site hasn't
    been updated to pass it — never conflate the two zero-like states.
- **`outstanding_possible_fill`** — `requested_quantity -
  confirmed_cumulative_fill` at the moment this row was written. This
  is genuinely uncertain, *not-yet-zero* exposure: the quantity that
  could still be confirmed by the broker while the order remains
  PENDING, and must be treated as live risk, not as safely absent.
  `0.0` once the order reaches a terminal status (FILLED/REJECTED/
  ERROR — nothing more can possibly fill). Reading this column off one
  row directly is discouraged for anything but forensic inspection —
  call `SignalStore.get_outstanding_possible_fill` for the live,
  aggregated figure the capital allocator and position-detail UI
  actually depend on.

**Caller contract** (`save_order_result`'s own docstring in
`app/db.py`): a PENDING order with no confirmed fill yet
(`result.filled_quantity is None`) must pass `applied_quantity=None`
(or omit it) — matching the fact that `record_fill` was correctly
**not** called for it. Passing a non-`None` `applied_quantity` implies
`record_fill` really ran with that exact amount.

**Managed-lifecycle orders** (TRK-22): `app/engine.py`'s
`_handle_managed_entry`/`_handle_managed_close` now populate these same
four columns too, via a `_ManagedOrderOutcome` bundle their shared
callers (`_handle_signal`'s managed branch, `close_position`) pass
straight through to `save_order_result` — before this, every
managed-lifecycle order left `applied_execution_delta` permanently
`NULL`, which meant Track 16's `/positions/{symbol}/provider-
allocations` and Track 18's `get_provider_position_ownership` (both
built on it) never saw managed-lifecycle activity at all, only
plain-account fills. The values are computed from the exact same
FILLED/PENDING classification `_submit_order` uses for the plain-account
path, adapted to each method's own pre-existing fill-application call
(`on_entry_fill`, `resolve_pending_entry`, `request_exit`/
`_apply_exit_fill`) rather than a second, duplicate `record_fill` call —
see `_ManagedOrderOutcome`'s own docstring in `app/engine.py` for the
full per-branch contract.

## `positions.net_quantity`: the exact update contract

`net_quantity` **is** `actual_remaining_ownership` by contract (AUD-01
onward): it is updated **only** from a broker-confirmed fill —
`record_fill` called with a real `confirmed_cumulative_fill`-derived
delta — **never** from a PENDING order's merely-requested quantity. A
broker that reports PENDING rather than a synchronous confirmed fill
(SignalStack, Alpaca, IBKR, NinjaTrader, Rithmic) leaves this column
**unchanged** until `app/reconciliation.py` (or a synchronous
partial-fill report alongside PENDING) confirms a real quantity. The
service's own belief about "what could still fill" lives separately in
`orders.outstanding_possible_fill` / `get_outstanding_possible_fill`,
never folded into `net_quantity` itself.

Positive = net long, negative = net short, zero = flat. This is the
service's own record of what it has sent and confirmed — never a live
read of the broker's actual position (there is no reconciliation loop
that treats a broker readback as authoritative over this column,
beyond what `app/reconciliation.py` explicitly performs).

## `orders.purpose` and `orders.family_id`

- **`purpose`** — WHY this order was placed, set at the exact call
  site that decided to place it (never inferred later from
  side/status, which can't distinguish e.g. an entry from a close on
  the same symbol/side). Real values populated today: `entry` (a fresh
  position-opening order, from a BUY/SELL signal) or `close` (a
  position-reducing order, from a CLOSE signal or a manual
  flatten/exit). `protective_stop`/`stop_revision`/`target` are
  **reserved names** for the same concept applied to a managed
  position's stop/target orders, but those are tracked in
  `stop_target_events` instead — no call site writes an `orders` row
  for them on this branch. `NULL` for any order row saved before this
  column existed — never backfilled with a guess.
- **`family_id`** — an id shared by every order belonging to the same
  position episode, so a caller can group an entry with its eventual
  close without re-deriving that link from timing/quantity heuristics.
  - For an `entry` order: the entry's own originating `signals.id`
    (equal to that row's own `signal_id`, kept as a separate column
    anyway so a future family could span more than one signal).
  - For a **managed-lifecycle** `close`: the *same* value as its
    position's entry order (`PositionPlan.entry_signal_id`, persisted
    for exactly this).
  - For a **plain** (non-managed_lifecycle) account's close: `NULL` —
    a plain position has no tracked lifecycle object linking it back
    to whichever single or accumulated entry fill(s) produced it. An
    honest, disclosed gap, not a fabricated link.

## `orders.reserved_notional`

Bounded (E03): set on this order row **only** when `result.status` is
PENDING and there's a real `result.broker_order_id` to poll — i.e.
only when `app/reconciliation.py`'s per-pending-order loop is
guaranteed to eventually observe this order's terminal status and
release it via `_correct_position`. Every other outcome (REJECTED /
ERROR / FILLED, or a PENDING with no `broker_order_id` to ever poll)
must release its reservation *immediately at the call site* instead
and pass `None` here. This is the `orders`-table twin of
`capital_reservations`' own reservation, written *after* the broker
call returns (unlike `capital_reservations`, written before) — see
`docs/design/CAPITAL_ALLOCATION.md`.

## `command_ledger.remote_identifiers` and `terminal_evidence` (JSON shape)

Both columns are JSON objects, **never** `NULL` — `'{}'` when nothing
is known yet — and are always valid JSON, read with `json.loads` only
by `SignalStore`'s own read/write helpers.

- **`remote_identifiers`** — whatever broker order id(s) become known
  once the broker responds, never fabricated before then. Populated by
  `classify_order_result`/`classify_cancel_result`/
  `classify_optional_order_result` (`app/command_ledger.py`) as:
  `{"broker_order_id": result.broker_order_id}` when a broker order id
  exists, else `{}`.
- **`terminal_evidence`** — whatever real evidence resolved (or
  currently characterizes) this row's state, shaped per outcome:
  - FILLED → `{"broker_status": "filled", "filled_quantity": ...,
    "filled_price": ..., "message": ...}`
  - REJECTED → `{"broker_status": "rejected", "message": ...}`
  - PENDING with a broker_order_id → `{}` (nothing terminal yet;
    `uncertainty_state` is `submitted_unconfirmed`)
  - PENDING with no broker_order_id → `{"broker_status":
    "pending_no_broker_order_id", "message": ...}`
  - ERROR → `{"broker_status": "error", "message": ...}`
  - a broker call that raised → `{"broker_status": "exception",
    "exception_type": ..., "message": ...}` (from
    `ambiguous_evidence_for_exception`)
  - a confirmed cancel → `{"broker_status": "cancelled"}`; an
    unconfirmed cancel → `{"broker_status": "cancel_not_confirmed"}`
  - a capability the broker adapter doesn't implement at all (base
    class returns `None` rather than an `OrderResult`) →
    `{"broker_status": "capability_not_supported"}`, paired with
    `REJECTED_CONFIRMED` (a definite, immediate, local "never
    attempted" answer — not `UNKNOWN_AMBIGUOUS`, which would wrongly
    suggest reconciliation could ever resolve it).

  Despite the name, `terminal_evidence` is written even alongside
  `UNKNOWN_AMBIGUOUS` (a state that is, by definition, *not* terminal)
  — the field name matches the column, not a claim of resolution.

## `command_ledger.request_fingerprint` and `idempotency_key`

- **`idempotency_key`** — UNIQUE across the whole table. The caller's
  own key for one logical command attempt; a retried call with the
  same key never submits a second broker order.
- **`request_fingerprint`** — a deterministic SHA-256
  (`compute_fingerprint`, `sort_keys=True, default=str`) over the
  exact request parameters. The same key arriving twice must hash to
  the same fingerprint if it's really the same request repeated (a
  legitimate retry); any difference means the key was reused for a
  genuinely different command, which is a caller bug surfaced as
  `CommandFingerprintMismatch`, never silently allowed through.

## `command_ledger.uncertainty_state` — the real enum values and meaning

From `app/models.py`'s `UncertaintyState`:

| value | terminal? | meaning |
|---|---|---|
| `pending_submission` | no | written and committed BEFORE the broker call — the pre-effect durable intent. Never observed in-process after `open_command_ledger_entry` returns; only a crash/restart leaves this as the row a recovery reader finds. |
| `submitted_unconfirmed` | no | the broker call returned a real `broker_order_id` but no terminal fill/reject yet — expected to resolve via reconciliation. |
| `confirmed` | **yes** | a definite, broker-confirmed terminal success (FILLED, or a confirmed cancel/replace). |
| `rejected_confirmed` | **yes** | a definite, broker-confirmed terminal rejection — nothing was ever accepted at the venue. |
| `unknown_ambiguous` | no | the critical state the audit named: the broker call raised, timed out, or returned PENDING/ERROR with no `broker_order_id` to poll — genuinely unknown whether the request reached (and was accepted by) the venue. Never silently dropped or treated as either success or failure; resolved only by independent reconciliation. |

`TERMINAL_UNCERTAINTY_STATES = {CONFIRMED, REJECTED_CONFIRMED}` is the
exact set `resolved_at` being non-`NULL` corresponds to.

## `capital_reservations.resolved_at`

`NULL` means "still an uncertain external effect" — released only once
this exact admission's outcome is confirmed terminal (REJECTED/ERROR/
FILLED, or a PENDING with nothing left to ever poll) at the same call
sites that already call `CapitalAllocator.release`. `CapitalAllocator.
__init__` sums every still-unresolved row here, per account, to
rebuild its in-memory ledger on startup — see
`docs/design/CAPITAL_ALLOCATION.md`.

## `stop_target_events.price` / `previous_price`

Both nullable, and their meaning is **event-type-dependent**
(`StopTargetEventType`):

| `event_type` | `price` | `previous_price` |
|---|---|---|
| `stop_placed` | the newly-confirmed stop price | this same `StopRecord`'s last broker-confirmed price before this call; `NULL` for a true initial placement |
| `stop_tightened` | the new (tighter) price | the price it replaced |
| `protection_failed` | the price that was attempted | mirrors `stop_placed`'s meaning |
| `target_hit` | the target's own trigger price | always `NULL` (a target firing has no "previous target price") |

## `writer_lease.fencing_token`

The only ever-increasing field on the singleton row. Not a lease-TTL
mechanism by itself — see `docs/design/WRITER_FENCING.md` for why
`require_active()` compares this value directly rather than consulting
`expires_at`. `expires_at` matters only to `promote_writer_lease`'s own
expiry check, a distinct concern from per-command fencing.

## `route_qualifications.state` — the real ladder values

From `app/qualification.py`'s `QualificationState`, in strict
prerequisite order: `implemented` → `configured` → `authenticated` →
`account_entitled` → `protocol_tested` → `venue_tested` →
`release_approved`. States at or above `account_entitled`
additionally require `BrokerAdapter.has_account_order_position_feedback`
to be true for that adapter — see ADR-0006. `release_approved` is
written only by an owner-gated, human-initiated `POST /qualifications`
call; nothing in this codebase writes it automatically.

## `config_accounts.management_recipe` and `qualification_level`

- **`management_recipe`** — `ManagementRecipe.FULL_MANAGED_LIFECYCLE`
  or `ManagementRecipe.PLAIN_UNMANAGED` (see ADR-0008). `NULL` on a row
  that predates this column means "not yet declared"; when read back
  through `app/routing.py`'s `*_from_store` loader, it is filled from
  `managed_lifecycle` the same way `DestinationAccount.__post_init__`
  does for a fresh in-memory construction, so a live caller never sees
  an unresolved `NULL`.
- **`qualification_level`** — a simple, free-form label (e.g.
  `"qualified"`, `"unqualified"`, `"pending_review"`), intentionally
  **not** an enum. `NULL` means "not yet declared," never fabricated as
  `"qualified"`. Distinct from `route_qualifications`' own structured
  ladder (ADR-0006) — this is the account's own simple, independent
  label, which may later reference that taxonomy but does not today.
