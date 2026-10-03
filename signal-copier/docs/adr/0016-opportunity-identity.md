# ADR-0016: Opportunity identity for budget reservations and order intents

Date: 2026-10-03
Status: Accepted
Deciders: WC-30, WC-31, WC-33 implementation

## Context

Budget reservations (WC-30) and order intents (WC-31) enforce a mutual-exclusion
guarantee: no two reservations/intents for the same trading opportunity (signal +
account pair) can exist in the `CLAIMED` state simultaneously. This prevents
double-booking of capital and ensures "never submit the same signal twice on the
same account" via a database UNIQUE constraint on `opportunity_id`.

The trading opportunity itself has two distinct forms:
1. **Single-selection** (ADR-0012): one signal routes to one account from an ordered
   pool of alternatives. The signal ID itself is the opportunity, since every
   signal has at most one confirmed, durable binding to one account.
2. **Replicate** (ADR-0012): one signal explicitly fans out to multiple accounts
   via a `delivery_mode: replicate` routing rule. Each replica is its own distinct
   opportunity with the same signal but different account.

## Decision

### Opportunity identity function

```python
def _opportunity_id_for(signal: Signal, account: DestinationAccount, single_ids: set[str]) -> str:
    """Canonical signal/account pair for budget/intent mutual exclusion."""
    if account.account_id in single_ids:
        return signal.id  # Single-selection: signal ID alone
    return f"{signal.id}:{account.account_id}"  # Replicate: composite
```

### Invariants

1. **Unique claim per opportunity** — At most one non-RELEASED `budget_reservations`
   row exists per `opportunity_id` (enforced by UNIQUE constraint). A redelivery
   of the same signal on the same account resumes the existing reservation (crash
   recovery pattern, ALLOC-07), never attempts a second one.

2. **Single-selection use signal ID alone** — For a single-selection routing rule,
   the signal's own ID is the opportunity since the rule itself enforces "one
   signal, one account." Using just the signal ID prevents accidental false
   uniqueness violations if the same signal is evaluated across multiple accounts
   in the same pool (e.g., during a retry after the first account was rejected) —
   only the finally-selected account's submission is recorded.

3. **Replicate destinations use composite key** — For an explicit `delivery_mode:
   replicate` rule, each destination is independent: two orders for the same
   signal on different accounts are deliberately two separate opportunities. The
   composite `<signal_id>:<account_id>` key enforces "never submit the same signal
   twice on the SAME account" even when replicating across many accounts.

4. **Crash-resumable allocation** — When a process restarts after a partial
   execution (e.g., reservation created but broker order never submitted due to a
   crash), the recovery path (`_check_and_reserve_resources`) calls
   `get_active_reservation_for_opportunity(opportunity_id)` and resumes it without
   allocating capital a second time. The command ledger downstream decides whether
   the broker call may still proceed (idempotency via client order ID prevents
   double-submission even if the ledger row exists).

## Consequences

### Positive
- UNIQUE constraint on `opportunity_id` provides atomic mutual exclusion, no
  application-side lock needed in the database.
- Single-selection pools remain efficient (no false collisions) while replicate
  destinations remain independent.
- Crash recovery is automatic: the same `opportunity_id` always maps to the same
  reservation, never spurious conflicts.

### Tradeoffs
- The deduplication key format differs between single/replicate modes. Code must
  know the routing rule's `delivery_mode` to construct/interpret the key.
- A signal that was planned as single-selection cannot later be rerouted to a
  different account via a second `opportunity_id` — if the selected account fails,
  the intent is marked skipped, not retried on the next account. This is
  intentional (ADR-0012: "selection is not failover") but worth noting.

## Implementation

- `app/engine.py`: `_opportunity_id_for` function computes the key
- `app/db.py`:
  - `budget_reservations.opportunity_id` (TEXT NOT NULL UNIQUE)
  - `order_intents.opportunity_id` (TEXT NOT NULL UNIQUE)
  - `get_active_reservation_for_opportunity(opportunity_id: str)`
- `app/workflow/budgets.py`: `HierarchicalBudget.check_and_reserve` uses this
  opportunity ID for the UNIQUE check
- Tests: `tests/test_wc03_hierarchical_budgets.py` (budget reservation identity),
  `tests/test_wc06_intents_and_outbox.py` (intent identity)

## Related Decisions

- **ADR-0012** (single-destination allocation): selects which account
- **ADR-0015** (workflow contract pipeline): hierarchical budgets and intents
- **WC-33** (engine wiring): opportunity identity for crash resume (ALLOC-07)
