# ADR-0006: A separate, venue-qualified qualification ladder, independent from implementation-derived capability inference

Status: Accepted
Date: 2026-09-29

## Context

`app/brokers/base.py` already has a "capability introspection"
mechanism (`has_protective_stop_capability` and similar) that answers
"does this adapter CLASS have a real method override" — a purely
code-derived, honest fact, computed from the Python class itself. An
external audit found this alone was being mistaken for something it
never claimed to be:

> "The broker capability matrix is implementation-derived, not
> venue-qualified... Add separate states for implemented, configured,
> authenticated, account-entitled, protocol-tested, venue-tested and
> release-approved."
>
> "Live qualification must be per exact route. Alpaca equities, IBKR
> options, a CCXT spot exchange, a CCXT perpetual exchange, MT5 and
> SignalStack are different execution products. One generic adapter
> passing tests cannot qualify the others."

A method override existing on a broker adapter class says nothing
about whether a *specific account*, on a *specific venue*, trading a
*specific product*, has actually been verified end-to-end — configured
with real credentials, authenticated, entitled for that instrument
type, exercised by this repo's own tests, tested against the real
venue, and signed off by a human for live trading.

## Decision

`app/qualification.py` defines a strict, sequential seven-state ladder
(`QUALIFICATION_STATE_ORDER`): `implemented` → `configured` →
`authenticated` → `account_entitled` → `protocol_tested` →
`venue_tested` → `release_approved`. A qualification is tracked per
exact **route** — the tuple `(adapter_type, route_key, asset_class,
product_type)` — so e.g. `("ccxt", "ccxt_binance_spot", "crypto",
"spot")` and `("ccxt", "ccxt_binance_perp", "crypto", "perpetual")` are
different routes even though both run through the identical
`CCXTBroker` class; qualifying one proves nothing about the other.

`route_qualifications` rows are append-only, one per (route, state)
ever achieved, recorded and gated by
`SignalStore.record_route_qualification`, which enforces the ladder
from the write path itself (never bypassable, including a direct call
with no HTTP layer involved): recording state N requires every state
0..N-1 already recorded for that same route. States at or above
`account_entitled` (`FEEDBACK_DEPENDENT_FLOOR`) additionally require
the adapter to have real account/order/position feedback capability
(`BrokerAdapter.has_account_order_position_feedback`) — you cannot
honestly claim entitlement, a real protocol test, or a real venue test
without some way to read back what actually happened.

`release_approved` is a deliberate human sign-off, not a technical
check — `POST /qualifications` is owner-gated (CSRF-protected
session), and nothing in this codebase ever writes a qualification row
on its own initiative.

This is explicitly kept as a separate, additive concept from
`ManagementRecipe` (ADR-0008) — a related but distinct taxonomy for a
different question (an account's own declared management contract, not
a route's venue-verification state) that may reference this ladder
later, but does not replace it.

## Consequences

- `has_protective_stop_capability`-style introspection remains exactly
  as it was — real, honest, code-derived — and is never removed or
  reinterpreted as venue evidence; the two mechanisms coexist because
  they answer different questions.
- A route can be `implemented` and `configured` (code exists, config
  exists) while remaining entirely unqualified for live trading —
  `venue_tested` is almost always NOT achieved in a sandbox/CI
  environment with no live credentials, which is the honest, expected
  state, not a bug to fix.
- The ladder's ordering is enforced structurally at the persistence
  layer, so no UI or API caller can skip straight to a high state for
  a route that was never even authenticated.
- Qualification state must be re-established per exact route — adding
  a new venue/product variant on an already-qualified adapter class
  starts that new route's ladder at zero, by design.
