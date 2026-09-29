# Testing Standards

~1,059 test functions across 161 files in `tests/`. This document
describes the real house standard this suite follows — evidenced by
commit messages, test docstrings, and `pytest.ini`/CI config — not a
generic testing checklist.

## The house standard: load-bearing verification

Every commit that fixes a real bug and adds a regression test for it in
this codebase's history states, explicitly, that the new test was proven
to actually catch the bug it claims to catch — by breaking the fix again,
watching the test fail for the *right* reason, then restoring the fix and
reconfirming green. This is not incidental; it is called out by name in
commit messages:

> **P0-9** ("Fail-closed sweep... fix silent zero->unknown coercion"):
> "Load-bearing verification: reverted the ibkr.py fix alone, confirmed
> `test_get_order_status_filled_with_a_genuine_zero_fill_stays_zero`
> fails with `assert None == 0.0` (the exact old bug), then restored the
> fix and reconfirmed green."

> **P0-3** ("Capital allocator: fail-closed sizing..."): "Load-bearing
> verification: reverted each of the two named bugs' fixes in turn,
> confirmed the corresponding new test genuinely fails for the right
> reason, then restored and reconfirmed green."

> **AUD-01** ("Replace optimistic PENDING-order accounting..."): two
> existing suites "asserted the old optimistic behavior by name
> (`# optimistic`) — corrected to the new fail-closed contract, verified
> to genuinely fail against the reverted optimistic code first."

**A test that has never been watched to fail is not trusted here.** A
green test suite proves nothing about a test that would also pass if the
code under test did nothing at all — a test that asserts on the wrong
thing, or that never exercises the failure path, is worse than no test:
it creates false confidence. The standard this codebase follows for any
new regression test on a load-bearing invariant (fencing, capital
admission, fill quantity truthfulness, idempotency, reconciliation
correctness):

1. Write the test against the **fixed** code; confirm it passes.
2. Temporarily revert (or hand-edit) just the fix, keeping the test
   unchanged.
3. Run the test and read the failure. It must fail *because of the exact
   bug being guarded against* — a wrong assertion value, a wrong
   exception type, or an unhandled exception that maps directly to the
   original defect (e.g. `assert None == 0.0`, not an unrelated
   `AttributeError` from a typo).
4. Restore the fix. Reconfirm green.
5. State that this was done — in the commit message for a real fix, or in
   a PR/review note otherwise. "Load-bearing verification: ..." is this
   codebase's own phrase for it; reuse it.

This is most important for tests that guard a single-point-of-failure
safety invariant — writer fencing, capital exposure ceilings, fail-closed
admission gates, idempotency/duplicate-submission guards — where a
silently-wrong test would let the exact class of bug this suite exists to
catch back in undetected. It is proportionate: a straightforward test of
a pure function's happy path doesn't need a ceremony write-up, but
anything added specifically *because* a real bug was found does.

## `pytest.importorskip` for optional runtime dependencies

`ccxt`, `ib_async`, `async_rithmic`, `twilio`, and `tweepy` back broker/
source adapters that are optional at runtime — `ccxt` isn't even in
`requirements.txt` (commented out; see `docs/standards/CODING.md`). Every
test file that needs one of these guards itself with
`pytest.importorskip(...)` at module scope when the whole file needs it
(`tests/test_ccxt_broker.py`, `tests/test_ibkr_broker.py`,
`tests/test_rithmic_source.py`) or inside individual test functions when
only part of the file does
(`tests/test_account_balance_capability.py`,
`tests/test_broker_capability_gate.py`,
`tests/test_adp02_adp06_bracket_capability_verification.py` — which skips
`ib_async` for one test and `ccxt` for another in the same file). Always
comment *why* it's optional: `# optional dependency -- not installed in
CI (see requirements.txt)`. Never wrap one of these imports in a bare
`try/except ImportError: pass` that silently drops the whole test — use
`importorskip` so a missing optional dependency shows up as an explicit
`SKIPPED` in the run, not a quietly absent test.

## CI's exact commands (source of truth: `.github/workflows/signal-copier-ci.yml`)

Reproduce CI locally with these exact commands, run from `signal-copier/`:

```bash
# Lint (F = pyflakes real-bug detectors, B = bugbear; not the opinionated
# style families — see pyproject.toml's [tool.ruff.lint] comment)
ruff check .

# Type check — an explicit, curated file list, NOT `mypy app/` — see
# below for why
python -m mypy app/db.py app/engine.py app/reconciliation.py app/lifecycle/manager.py app/lifecycle/models.py \
     app/routing.py app/risk.py app/models.py app/main.py app/backtest/simulator.py app/sources/webhook.py \
     app/sources/whatsapp.py app/sources/sms_twilio.py app/sources/ninjatrader.py \
     app/sources/text_parser.py app/sources/base.py app/brokers/alpaca.py app/brokers/base.py \
     app/brokers/paper.py app/brokers/ccxt_broker.py app/brokers/signalstack.py app/brokers/ninjatrader.py \
     app/config.py app/auth.py app/rate_limit.py app/context/sec_edgar.py app/context/fred.py app/context/fx.py \
     app/capital_allocator.py app/provider_value.py app/provider_scout.py \
     --follow-imports=silent

# Tests
pytest -q

# Dependency advisory audit (one CVE is deliberately ignored — see the
# workflow file's own comment on CVE-2026-49265 / tweepy's oauthlib pin)
pip-audit -r requirements.txt --ignore-vuln CVE-2026-49265
```

mypy's file list is curated, not `app/**/*.py` — it covers the core
execution/financial-integrity modules and the broker/source adapters CI
can actually install (no `ib_async`/`async_rithmic`/`tweepy`/`ccxt`
imports required at type-check time for the files not listed), plus the
config/auth/rate-limit/context modules. When you add a new module to one
of the *covered* families (a new file under `app/brokers/` or
`app/sources/` that doesn't require an optional dependency, or a new core
engine/lifecycle module), add it to this list — an uncovered financial
module is a real gap, not an oversight to leave alone.

A separate `secret-scan` job runs `gitleaks` against
`signal-copier/.gitleaks.toml` on every push/PR — never commit a real
secret expecting CI to catch it as a courtesy; this is the actual
enforcement.

Mutation testing (`mutmut run`) is scoped, on purpose, to `app/auth.py`
alone against `tests/test_owner_auth.py` and
`tests/test_sms_twilio_sender_authorization.py` — see
`pyproject.toml`'s `[tool.mutmut]` comment for why it isn't run in CI
(re-running per-surviving-mutant against the full ~1,000-test suite
across a larger module would turn a routine check into a multi-hour job)
and why auth specifically ("100% line coverage says every line ran, not
that every line's *behavior* is actually asserted on"). It is a local,
periodic check, not a CI gate — run it yourself before/after touching
`app/auth.py`.

## Fixture isolation

See `docs/testing/FIXTURES.md` for the full convention (the `store`
fixture, and the module-level-singleton sharp edge). Summary: prefer a
fresh, `tmp_path`-backed `SignalStore` per test over anything that reads
or writes the shared `app.main` module-level singletons
(`store`, `engine`, `lifecycle_manager`, `writer_lease_guard`, `brokers`)
directly — those singletons are constructed once at import time and are
genuinely shared across every test in the same process unless a fixture
explicitly rewires them with `monkeypatch.setattr`.

## Async testing

`pytest.ini` sets `asyncio_mode = auto` — an `async def test_...` needs no
`@pytest.mark.asyncio` decorator, it's picked up automatically. One
warning filter is deliberately silenced, with a comment explaining why it
reflects the test harness rather than a real bug: `app/context/*.py`'s
module-level `AsyncLimiter` instances are created once at import time (one
continuous event loop in production) but pytest-asyncio gives each test
function its own event loop, so aiolimiter warns about cross-loop reuse
even though no real state (no pending waiters) carries over between
tests.

## The full test-file naming convention

See `docs/standards/NAMING.md` for `test_<ticket-id>_<description>.py`.
