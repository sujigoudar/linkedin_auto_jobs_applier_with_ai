# Current blockers

Honest list — this project does not use a "Bucket-A"-style formal
blocker taxonomy anywhere in its own docs or commit history (searched
and not found), so this is a plain list of real, currently-true
blockers rather than a reconstruction of a naming scheme that doesn't
exist here.

## Cannot be exercised live from a sandboxed environment

Two broker adapters have **no sandbox/paper environment at all** — every
request they make, including a first test, hits a real account with real
money:

- **`app/brokers/schwab.py`** — no official Schwab trading API client and
  no sandbox; the adapter's own docstring states this plainly and warns
  to read it in full before ever wiring real credentials.
- **`app/brokers/robinhood.py`** — Robinhood has never published an
  official trading API, has no sandbox, and using it programmatically is
  outside Robinhood's own Terms of Service; the adapter's own docstring
  states this and requires the risk be explicitly accepted.

Neither of these can be meaningfully integration-tested end-to-end
(beyond unit-level mocking of their HTTP calls) from this or any other
sandboxed development environment — doing so would require live broker
credentials and real financial risk that this environment is not, and
should not be, set up to provide. Any task that would require "confirm
this actually places a real order against Schwab/Robinhood" is blocked
on that basis, permanently, by design — not something to work around.

## Genuinely open engineering gaps (not sandbox-related, but currently unresolved)

- **`trading_authority` wiring** — see `docs/state/tasks.json`'s
  `trading_authority-wiring-to-P0-6` entry. Not blocked on anything
  external; simply not yet done despite its prerequisite (P0-6) having
  landed.
- **P0-7 (qualification/release-approval taxonomy)** — not started on
  this branch. `release_status` remains an honest placeholder until it
  lands.
- **NinjaTrader signal-source, NinjaScript side** — `app/sources/ninjatrader.py`
  and its fresh-written `ninjascript/SignalCopierAutoJournal.cs` exist,
  but the C# side has never been compiled or run against a real or
  Sim101 NinjaTrader 8 install, because there is no NinjaTrader and no
  Windows environment available here. This is a genuine, environment-
  based blocker on final verification, not on the code existing —
  README.md's older "genuinely open" framing is now stale (see
  `docs/state/tasks.json`).

## Known CI noise (not a blocker, but worth distinguishing from a real failure)

`tests/test_c07_context_rate_limiting.py` can occasionally fail on wall-
clock timing margin under heavy CI load — see
`docs/process/DEFINITION_OF_DONE.md` item 3 for the accepted mitigation
(rerun standalone to confirm). This should never be treated as a reason
to skip or weaken the test; it's an accepted characteristic of a test
that deliberately measures real timing, not evidence of an actual
regression.
