# Glossary

Precise, codebase-specific definitions — pulled directly from the module
docstrings and code that define each term, not generic trading
vocabulary. Where a definition is quoted, it's paraphrased for length but
faithful to the source; read the cited module for the full context.

### Managed lifecycle (`managed_lifecycle` / `ManagementRecipe.FULL_MANAGED_LIFECYCLE`)

An account opted into `app/lifecycle/manager.py`'s `PositionLifecycleManager`
instead of plain BUY/SELL/CLOSE: entries are protected the moment a fill
is actually confirmed (not just at the end), targets/trailing are
evaluated as logical, app-side instructions rather than resting broker
orders, and every exit is coordinated through one `CloseArbiter`. Gets
MAE/MFE tracking, protection-coverage detail, and partial-fill-during-
transfer correctness that a plain account does not.

### Plain / unmanaged (`ManagementRecipe.PLAIN_UNMANAGED`)

An account whose entries/exits are ordinary BUY/SELL/CLOSE orders against
`SignalStore`'s own tracked `positions` table — no lifecycle manager, no
MAE/MFE, no protection-coverage tracking, no transfer logic. A `CLOSE`
signal on a plain account additionally requires the P0-5 reconciliation
gate (see "Exclusive-writer qualification" below) before it's allowed to
proceed.

### Management recipe (`DestinationAccount.management_recipe`)

The account's own explicit, **persisted** declaration of which of the two
products above it is (`app/models.py`'s `ManagementRecipe`). Defaults from
the `managed_lifecycle` boolean when not given explicitly, but can be set
independently — a mismatch between the two is a real misconfiguration
worth surfacing, not something silently resolved.

### Qualification level (`DestinationAccount.qualification_level`)

A simple, free-form label (e.g. `"qualified"`, `"unqualified"`,
`"pending_review"`) on an account's own declared management contract.
Deliberately not an enum. Distinct from the route qualification ladder
below, which qualifies broker/exchange execution *routes*, not accounts.

### Capital reservation

A provisional hold on notional exposure, taken by `app/capital_allocator.py`
at admission time and released once the order's outcome is known. For a
`PENDING` order that carries a real `broker_order_id`, the reservation is
kept — not released immediately — until `app/reconciliation.py`'s polling
loop confirms the order's terminal status, so combined confirmed +
pending exposure can't briefly exceed a configured ceiling. Persisted in
`orders.reserved_notional` (plain accounts) or
`PendingEntry.reserved_notional` (managed-lifecycle accounts).

### Writer lease / fencing token

The mechanism in `app/writer_lease.py` that makes single-writer status
across an active/standby multi-site deployment fail *safe* rather than
fail *silent*. The shared, Litestream-replicated SQLite database is the
only cross-host coordination point; a monotonically increasing fencing
token is issued exactly once per takeover (`app/promote_cli.py`, always a
deliberate human action) and every command-execution path re-checks the
CURRENT token immediately before acting. A stale writer's own commands
are refused the instant a new token exists — this is a second, automatic
guard underneath, not a replacement for, `deploy/RUNBOOK.md`'s manual
promotion procedure (see docs/FAILOVER.md).

### Command ledger

The durable, pre-effect record (`command_ledger` table,
`app/command_ledger.py`) written and committed **before** every real
broker command call site (entry, close, stop change, replace, cancel,
flatten) in `app/engine.py` and `app/lifecycle/manager.py`. Exists so a
process crash between "decided to submit" and "the broker call returned"
still leaves a durable trace of the attempt, instead of nothing.

### Uncertainty state

The lifecycle of one `command_ledger` row's knowledge of whether its
command actually happened at the broker
(`app/models.py`'s `UncertaintyState`):

- `pending_submission` — committed before the broker call; never observed
  after the call returns in-process.
- `submitted_unconfirmed` — the broker returned a real `broker_order_id`
  but no terminal fill/reject yet; expected to resolve via reconciliation.
- `confirmed` — a definite, broker-confirmed terminal success (terminal).
- `rejected_confirmed` — a definite, broker-confirmed terminal rejection
  (terminal).
- `unknown_ambiguous` — the broker call raised, timed out, or returned
  PENDING/ERROR with no order id to poll: genuinely unknown whether the
  request reached the venue. Never silently dropped or assumed either
  way; must be resolved only by independent reconciliation.

### Route (qualification sense)

The tuple `(adapter_type, route_key, asset_class, product_type)` used by
`app/qualification.py` — e.g. `("ccxt", "ccxt_binance_spot", "crypto",
"spot")` is a *different* route from `("ccxt", "ccxt_binance_perp",
"crypto", "perpetual")` even though both run through the same
`CCXTBroker` class. Each route's qualification is tracked and gated
completely independently.

### Qualification ladder

The strict, sequential prerequisite chain a route's qualification state
must climb, in order, enforced by
`SignalStore.record_route_qualification` (`app/qualification.py`'s
`QualificationState`): `implemented -> configured -> authenticated ->
account_entitled -> protocol_tested -> venue_tested -> release_approved`.
A route can't jump to a later rung without every earlier one already
recorded. `release_approved` is a deliberate human sign-off, not a
technical check. This is distinct from `BrokerAdapter`'s capability
introspection (`has_protective_stop_capability` etc.), which answers "does
this adapter *class* have a real method override" — useful engineering
metadata, but not evidence any particular account/venue/product has
actually been verified end to end.

### Capability introspection

`app/brokers/base.py`'s `has_*_capability` properties, computed by
checking whether a `BrokerAdapter` subclass actually overrides a given
base no-op method — never from a separately maintained boolean flag that
could silently drift out of sync with the code. Used both to gate live
admission (e.g. `can_protect_a_managed_position`) and to report a broker's
real capability matrix at `GET /brokers`.

### Order family (`orders.family_id`)

Groups every order belonging to the same logical position lifecycle (the
originating entry signal's id, or the entry signal id of an already-open
lifecycle for a subsequent exit) — used by the dashboard's TR-03/TR-06
views to show orders/fills grouped by the real position they belong to,
rather than as an unrelated flat list.

### Quantity ledger fields (`orders` table, per fill event)

Five distinct, persisted fields distinguishing what was asked for from
what actually happened (see `app/engine.py`'s "AUD-01" module-docstring
section):

- `requested_quantity` — what was asked for.
- `confirmed_cumulative_fill` — what the broker has actually confirmed
  filled so far for this order, exactly as reported.
- `applied_execution_delta` — the position-impacting change this specific
  fill event applied to `positions.net_quantity` (0.0, not a guess, when
  nothing was confirmed yet).
- `outstanding_possible_fill` — `requested_quantity -
  confirmed_cumulative_fill` while the order remains PENDING.
- `net_quantity` (on `positions`) — `SignalStore`'s own tracked net
  position, updated ONLY from a broker-confirmed fill, never
  optimistically from a merely-requested quantity.

### Owned / covered / uncovered / reserved quantity (managed lifecycle)

`PositionLifecycleManager`'s and `CloseArbiter`'s quantity-by-quantity
picture of a managed position, replacing a naive `protected: true/false`
flag:

- `owned_quantity` (`confirmed_owned_quantity`) — the real, confirmed
  remaining position size.
- `covered_quantity` — how much is behind a broker-confirmed protective
  stop right now.
- `uncovered_quantity` — `owned - covered`; genuinely unprotected shares,
  most commonly during an in-flight protection transfer.
- `reserved_quantity` (`CloseArbiter`) — the sum of exit quantities
  currently in flight for a `(account, symbol)`.
- `available_to_sell` — `confirmed_owned_quantity - reserved_quantity`,
  the invariant `CloseArbiter` enforces so two exits can never both claim
  the same shares.

### Protection transfer

The window during which a stop is reduced/cancelled to free shares for an
exit — genuinely non-instantaneous, so a partially-filled exit during that
window must have the stop restored against what was **actually
confirmed**, not what was requested (`PendingExit`/`TransferPhase` in
`app/lifecycle/models.py`).

### Exclusive-writer qualification (`DestinationAccount.exclusive_writer_qualified`)

An off-by-default, explicit, narrow operator assertion that **nothing
else writes to this specific broker account's position outside Signal
Copier**. For a plain account on a broker with no verified position-
readback capability, this is one of the two ways (the other being a
fresh, matching broker-position readback) a `CLOSE` is allowed to proceed
at all — see README.md's "Exclusive-writer qualification (P0-5)" section.

### Route qualification vs. account management-recipe

Two independent, non-overlapping concepts that are easy to conflate:
qualification (`app/qualification.py`) certifies a broker/exchange
*route*; management recipe (`app/models.py`'s `ManagementRecipe`)
declares which safety *product* a specific *account* is opted into.
Neither implies the other.
