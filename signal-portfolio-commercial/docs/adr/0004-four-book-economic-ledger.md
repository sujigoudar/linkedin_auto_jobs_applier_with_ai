# ADR-0004: Four-book economic ledger (SOURCE / MODEL / PLATFORM / FOLLOWER)

## Status

Accepted. Implemented in `app/models/ledger.py` (`Book` enum, `LedgerEntry`).

## Context

`spec/docs/04_metrics_accounting_and_truth.md` requires "source recommendations,
canonical portfolio model, platform strategy and actual follower execution" to be
kept as separate economic facts: "An alert delivered is not an executed trade.
[Platform] strategy model performance is not customer actual performance." A single
shared ledger, or a ledger that conflated any two of these, would make it impossible
to answer basic questions honestly — did a signal actually get filled? Did the
strategy's own model performance match what a real follower account actually
experienced? — without silently blending numbers that describe fundamentally
different things.

## Decision

`app/models/ledger.py::Book` defines exactly four independent books, each a real,
separately-populated dimension on `LedgerEntry.book`:

- **SOURCE** — what the provider/analyst originally recommended. Populated from
  `SOURCE_RECEIPT` events only when the recommendation itself carried a real
  quantity **and** price (S7: "SOURCE records what was recommended; it does not
  claim an execution" — a bare "buy AAPL" alert with neither has nothing to record
  numerically, and produces no ledger row, not a fabricated one).
- **MODEL** — the canonical portfolio-version model's own instructions (reserved;
  not yet populated by any ingest path in this build).
- **PLATFORM** — the owner's actual discretionary/strategy account, populated from
  real `EXECUTION_APPLIED` events and from an activated bootstrap snapshot's own
  baseline positions.
- **FOLLOWER** — a specific customer's actual executed account, populated only from
  an authorized observation of that specific customer's account
  (`follower_connection_id` set) — "a copied model alert or subscription alone
  cannot populate this book." Nothing in this build populates a FOLLOWER-book entry
  yet (`platform_connection.py`'s own docstring: a connection is only ever declared
  today, not a real authorized observation channel); the column exists so
  `app/services/customer_performance_state.py`'s query is correct today, ready for
  the real observation connector to populate it later.

Every `LedgerEntry` also carries `evidence_class` (from
`signal_platform_contracts.EvidenceClass` — synthetic fixture, internal paper
trade, hypothetical backtest, observed execution, platform-reported model result),
so a dashboard never has to guess what kind of evidence an entry represents from
context.

## Consequences

- The four books can never be summed together as if they were one fungible number —
  a report that wants "platform performance" and a report that wants "what customer
  X actually experienced" query different books by construction, not by convention.
- Adding a new book (e.g. a future benchmark book) is an enum addition plus a real,
  disclosed source of data for it — never an overload of an existing book's meaning.
- FOLLOWER-book reporting is honestly empty until the real observation connector
  (`app/services/follower_observation.py`, S12 step 6) ships; nothing in this
  codebase should claim customer-actual performance numbers before that lands.
