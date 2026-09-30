# Known issues

Genuine, disclosed gaps — pulled from module docstrings, commit
messages, and the CI workflow, not invented. See `docs/TECH_DEBT.md` for
the broader design-level debt list and `docs/state/PENDING_DECISIONS.md`
for open decisions.

## `/system/readiness`'s `trading_authority` is stale, not just incomplete

`app/main.py` reports `trading_authority` via a placeholder whose own
FOLLOW-UP comment says it's waiting on P0-6 (`writer_lease` fencing) to
land. P0-6 landed on this branch (`55976eb`) and the placeholder was
never updated to read the real `WriterLeaseGuard` state. Until this is
fixed, `/system/readiness` under-reports what this build can actually
prove about who holds write authority. See
`docs/state/PENDING_DECISIONS.md`.

## `release_status` has no real taxonomy yet

Same endpoint, `release_status` is an honest, currently-accurate
`not_tracked` placeholder — P0-7 (a qualification/release-approval
taxonomy) has not landed. Not a bug, but a real current limitation:
there is currently no automated way to ask this system "is this account/
strategy approved to go live."

## `tests/test_c07_context_rate_limiting.py`: a known wall-clock timing flake

Asserts real elapsed time against a deliberately tiny rate-limiter
window so it can prove throttling without waiting out production-scale
windows (1s/60s). Under heavy CI load it can occasionally miss its
margin. The accepted response is to rerun it standalone and confirm —
see `docs/process/DEFINITION_OF_DONE.md`. It is not disabled or loosened,
because doing so would also hide a genuine regression in the throttling
behavior it verifies.

## `oauthlib` CVE-2026-49265: no available fix

`.github/workflows/signal-copier-ci.yml`'s dependency audit explicitly
ignores `CVE-2026-49265` (oauthlib < 4.0.0). `tweepy` (the Twitter/X
source adapter's real dependency) currently pins `oauthlib<4,>=3.2.0`
with no released version compatible with `oauthlib>=4.0.0`, so this
cannot be closed by a version bump today. The CI workflow's own comment
says to re-check this the moment tweepy publishes a release without that
cap — this has not happened as of this branch's current head.

## NinjaTrader signal-source: NinjaScript side unverified

`app/sources/ninjatrader.py`'s Python side (parsing, the
`/ninjatrader/webhook` route) is real and tested. The accompanying
`ninjascript/SignalCopierAutoJournal.cs` has never been compiled or run
against a real or Sim101 NinjaTrader 8 install — there is no NinjaTrader
or Windows environment available to do so here. Treat it as reviewed,
not verified, until someone actually runs it against Sim101 first.

## Two broker adapters have no sandbox and real-money-only testing

`app/brokers/schwab.py` and `app/brokers/robinhood.py` have no
sandbox/paper environment at all; every call, including a first test,
uses a real account with real money. `robinhood.py`'s programmatic use
is also outside Robinhood's own Terms of Service. Both are explicitly
opt-in, risk-accepted integrations per their own docstrings — see
`docs/state/BLOCKERS.md`.

## Capital allocator: scope gaps disclosed in its own docstring

`app/capital_allocator.py` states plainly what it does not do:
multi-currency/FX conversion, per-analyst overlap accounting, portfolio
stress-loss modeling, and cross-account netting beyond plain summation.
See `docs/TECH_DEBT.md`.
