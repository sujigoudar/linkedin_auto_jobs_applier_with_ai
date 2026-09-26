"""Read-only market/economic *context* lookups — SEC filings, FRED macro
series, FX reference rates.

Deliberately isolated from everything else this app does: nothing in this
package is imported by app/engine.py, app/lifecycle/*, app/reconciliation.py,
or any app/brokers/*.py adapter, and nothing here can place, cancel, or
modify an order or a protective stop. It exists only to answer read-only
questions an operator might ask alongside the live position/signal data
already in the dashboard (e.g. "what did this company's last 10-Q say,"
"where are rates right now," "what's the reference EUR/USD rate") — never
to drive a trading decision automatically.

Each submodule wraps exactly one free, publicly documented API:
- sec_edgar.py: SEC EDGAR (data.sec.gov) — keyless, requires only an
  identifying User-Agent per SEC's fair-access policy.
- fred.py: the St. Louis Fed's FRED API — requires a free API key
  (FRED_API_KEY env var); the endpoint using it 501s cleanly if unset,
  same fail-closed-not-fail-open pattern as this project's other optional
  integrations (see README's "Owner authentication" section).
- fx.py: Frankfurter (frankfurter.dev) — fully keyless daily reference FX.

These three were chosen out of a much larger reviewed candidate list
specifically because they're free with no ambiguous "developer/non-production
only" restriction, keyless or simple free-registration, and don't
meaningfully overlap each other. See README.md's "Market/economic context"
section for what else was reviewed and deliberately not added.
"""
