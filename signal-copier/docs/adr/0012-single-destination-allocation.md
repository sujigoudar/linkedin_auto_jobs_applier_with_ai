# ADR-0012: One intended trade, one selected account

Status: accepted (owner directive 2026-10-02; ledger DL-01, DL-02, DL-05, DL-06)

## Context

Routing rules listed destination accounts and the engine placed one order per
destination, so one signal produced N executions. The README described this
as deliberate fan-out and a test asserted it. Idempotency, reservations and
ownership were account-scoped, so each copy looked individually correct.

## Decision

1. A routing rule's `delivery_mode` is `single` (default) or `replicate`.
2. Under `single`, destinations are alternatives in priority order. Matching
   `single` rules for a source merge into one ordered pool.
3. Each entry signal creates one durable `allocation_intents` row before any
   account-specific execution. The engine walks the pool, applying every
   pre-submission gate, and binds the first account that passes.
4. A submission attempt on the bound account (any outcome, including unknown)
   commits the intent. It is never rerouted. A restart or duplicate delivery
   resumes the bound account only; if the bound account leaves the approved
   pool the engine does not re-select.
5. If no candidate passes, the intent is `skipped` with each reason; no order
   is submitted.
6. `replicate` is the only fan-out and is never inferred from account count.
7. CLOSE signals keep their account-scoped behavior (each account exits only
   what it owns).
8. Strategy ceilings (`strategy_budgets`) are global and counted once across
   accounts; the check and reservation insert are one `BEGIN IMMEDIATE`
   transaction.

## Consequences

- Existing multi-destination rules change behavior on upgrade; owners who want
  fan-out must set `replicate`.
- Selection is not failover (ADR-0003 still forbids automatic failover).
- Ownership of same-instrument positions across providers (immutable
  allocation identity) is not solved by this ADR.
