# Naming Conventions

Observed, real conventions from this codebase's own file tree and git
history — not aspirational style-guide prose.

## Modules: snake_case, one concern per file

`app/` is flat-ish with a few concern-based subpackages
(`app/brokers/`, `app/sources/`, `app/context/`, `app/lifecycle/`,
`app/services/`, `app/backtest/`). Every module is `snake_case.py`, named
after the single concern it owns:

- `app/writer_lease.py` — writer-lease fencing only.
- `app/command_ledger.py` — the pre-effect command ledger only.
- `app/capital_allocator.py` — capital/risk admission gates only.
- `app/rate_limit.py` — HTTP ingress rate limiting only.
- `app/logging_config.py` — structlog configuration only.

Broker adapters live one-per-file under `app/brokers/` named after the
broker (`alpaca.py`, `ibkr.py`, `ccxt_broker.py`, `signalstack.py`,
`ninjatrader.py`, `rithmic.py`, `mt4_mt5.py`, `oanda.py`, `robinhood.py`,
`schwab.py`, `tastytrade.py`, `tradestation.py`, `tradovate.py`, `base.py`
for the shared `BrokerAdapter` protocol). Source adapters follow the same
pattern under `app/sources/` (`webhook.py`, `whatsapp.py`, `sms_twilio.py`,
`ninjatrader.py`, `rithmic.py`, `slack.py`, `discord.py`, `telegram.py`,
`twitter.py`, `text_parser.py`, `base.py`). `_broker` / `_source` is only
appended when the bare vendor name would collide with something else
(`ccxt_broker.py` — `ccxt` the library import already occupies the bare
name).

## Test files: `test_<ticket-id>_<description>.py`

The dominant pattern across `tests/` (161 files) ties a test file to the
audit/ticket ID whose finding it proves is fixed, followed by a
descriptive slug:

```
test_p0_5_close_reconciliation.py
test_p0_2_command_ledger.py
test_aud01_distinct_quantity_model.py
test_c06_ingress_rate_limiting.py
test_c22_structured_logging.py
test_c29_hypothesis_quantity_conservation.py
test_db01_foreign_key_enforcement.py
test_e02_classify_messages_endpoint.py
test_exe01_submission_response_lost.py
test_risk01_strict_financial_inputs.py
test_sig01_duplicate_submission_protection.py
test_tr01_allocation_donuts.py
test_adp02_adp06_bracket_capability_verification.py
test_ops01_ops02_ops03_worker_health_and_reconciliation.py
```

Notes on the ticket-id vocabulary actually used: `P0-N` (release-blocking
findings), `AUD-NN` (audit findings), `C-NN` (a numbered checklist item —
see the `C06`/`C07`/`C22`/`C35`/`C36`/`C37`/`C38` references in
`pyproject.toml` and the CI workflow), `DB-0x`, `E0N` (endpoint work),
`EXE-NN` (execution-integrity findings), `RISK-0N`, `SIG-0N` (signal
ingestion), `TR-NN` (trading-screen/UI work), `OPS-0N`, `ADP-0N` (adapter
capability verification), `PU-xN` (position/PnL tracking). A single test
file may cover *several* ticket IDs when they land together
(`test_b2_b6_reconciliation_and_lifecycle_previews.py`,
`test_adp02_adp06_bracket_capability_verification.py`,
`test_tr09_tr12_trading_screens.py`) — join them in filename order,
underscore-separated.

Not every file follows the ticket-id form — plain feature-named files are
equally normal for code that isn't tied to a specific audit finding
(`test_engine.py`, `test_capital_allocator.py`, `test_paper_broker.py`,
`test_writer_lease_fencing.py`, `test_reconciliation.py`). Use the
ticket-id form when the test exists *because of* a numbered finding —
this makes the regression traceable back to the finding it closes (see
`docs/testing/REGRESSIONS.md`) — and a plain descriptive name otherwise.

A few files are named after a git commit's short hash instead
(`test_5e91e78_lifecycle_composition.py`,
`test_edd2b70_review_regressions.py`) — an accepted but minority pattern,
used when a fix doesn't map cleanly to one ticket id.

## Alembic migrations: `NNNN_description.py`, strictly sequential

`alembic/versions/` uses a 4-digit zero-padded sequence number, not a
timestamp or a random hex revision id, followed by a lowercase
snake_case description of what the migration adds:

```
0001_initial_schema.py
0011_add_capital_reservations_table.py
0012_add_orders_distinct_quantity_fields.py
0013_add_route_qualifications_table.py
0014_add_command_ledger_table.py
0015_add_writer_lease_table.py
```

The sequence is a hard append-only contract: when two migrations are
developed in parallel and would otherwise claim the same number, the
later-landing one is renumbered rather than the earlier one being
disturbed — see this repo's own commit
"Renumber writer_lease migration to 0015 (0014 taken by command_ledger)".
Never reuse or skip a number, and never rename an already-merged
migration file (Alembic's revision chain depends on the file, not just
the number, staying stable once merged).

## Exception classes: `<Condition><Error>`, one per distinct failure mode

See `docs/standards/ERROR_HANDLING.md` for the full catalog. Naming
pattern: `<WhatWentWrong>Error` (`FencedOutError`,
`WriterLeaseHeldByAnotherSiteError`, `LeaseStillValidError`,
`CommandFingerprintMismatch`, `SignalValidationError`,
`QualificationError`) — the name states the condition, not the module it
lives in, so it reads correctly at a call site far from its definition.

## Config constants: `SCREAMING_SNAKE_CASE`, env-var-shaped

Module-level tunables that come from environment/config are named exactly
as their env var (`WRITER_SITE_ID`, `WRITER_LEASE_SECONDS`,
`WRITER_LEASE_RENEW_SECONDS`, `MAX_OWNER_NOTIONAL_EXPOSURE`,
`SEC_EDGAR_USER_AGENT`). Internal-only tunables that are not
environment-configurable are prefixed with a leading underscore to mark
them as a module-private default, e.g. `_MAX_CONCURRENT_PRICE_LOOKUPS`
(`app/pricing.py`), `_rate_limiter` / `_SECRET_KEY_SUBSTRINGS`
(`app/context/*.py`, `app/logging_config.py`).
