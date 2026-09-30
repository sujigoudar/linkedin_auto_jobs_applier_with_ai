# Definition of done

This is not aspirational. It is reconstructed from what
`.github/workflows/signal-copier-ci.yml` actually gates on and from what
this branch's own commit messages repeatedly report having checked before
calling a change finished. A change is not done until every item below is
true, in order.

## 1. Ruff clean

```
ruff check .
```

run from `signal-copier/` (CI's `working-directory`). See `pyproject.toml`
for the scoped ruleset. Commit `eaa5c80` ("Fix CI lint/type failures:
unused import, missing exception chain, loose dict typing") and
`78f87ab` ("Fix signal-portfolio-commercial CI: pin ruff ruleset, fix 3
real findings") are real examples of this gate catching something before
it landed.

## 2. Mypy clean, on the exact CI-scoped file list

Not `mypy .` — mypy here is intentionally scoped to the modules that
carry real financial/control logic, listed explicitly in
`.github/workflows/signal-copier-ci.yml`:

```
python -m mypy app/db.py app/engine.py app/reconciliation.py app/lifecycle/manager.py app/lifecycle/models.py \
     app/routing.py app/risk.py app/models.py app/main.py app/backtest/simulator.py app/sources/webhook.py \
     app/sources/whatsapp.py app/sources/sms_twilio.py app/sources/ninjatrader.py \
     app/sources/text_parser.py app/sources/base.py app/brokers/alpaca.py app/brokers/base.py \
     app/brokers/paper.py app/brokers/ccxt_broker.py app/brokers/signalstack.py app/brokers/ninjatrader.py \
     app/config.py app/auth.py app/rate_limit.py app/context/sec_edgar.py app/context/fred.py app/context/fx.py \
     app/capital_allocator.py app/provider_value.py app/provider_scout.py \
     --follow-imports=silent
```

Run this exact command, on this exact file list, before calling anything
done — a new module added to this critical path should be added to the
list, not left out because "the CI file list didn't cover it yet."
Commit `c59cd97` ("Fix CI: real mypy union-attr narrowing in
compute_publication_blockers") is a real example of this gate being
treated as load-bearing, not advisory.

## 3. Full pytest suite green

```
pytest -q
```

The whole suite, not just the tests for the file touched. The suite is
159 test files as of this branch's current head. Passing the tests you
wrote and skipping the rest is not done.

**The one documented, accepted exception**: `tests/test_c07_context_rate_limiting.py`
asserts real wall-clock timing (`elapsed >= 0.3` after three calls
through a rate limiter set to a tiny, artificial window) so it can prove
real throttling without waiting out the production 1s/60s windows. Under
heavy CI load this can occasionally miss its timing margin and fail —
this is a known, pre-existing flake in that one file, not a regression.
The correct response to a red run in exactly that file is to **rerun it
standalone** (`pytest -q tests/test_c07_context_rate_limiting.py`) and
confirm it passes in isolation before treating the run as green. A
failure anywhere else, or a repeated failure in that file even in
isolation, is a real failure and blocks done.

## 4. A genuine load-bearing verification was performed

Passing tests are necessary but not sufficient — a test that was never
confirmed to fail against the old/broken behavior might be passing for
the wrong reason (tautologically, or against code that never ran). The
practice this branch's history repeatedly follows, stated explicitly in
commit messages:

1. Temporarily revert or break the specific invariant the change claims
   to fix.
2. Run the new test and **confirm it fails, for the right reason** (not
   an unrelated error).
3. Restore the fix.
4. Re-run and confirm green.

Real examples, quoted directly from this branch:

- `ae6a016` (command_ledger): *"Verified load-bearing: temporarily moved
  the ledger write in `_submit_order` to after the broker call and
  confirmed a simulated mid-call crash then loses the row entirely;
  reverted and reconfirmed."*
- `c6e4e7b` (capital allocator fail-closed sizing): *"Load-bearing
  verification: reverted each of the two named bugs' fixes in turn,
  confirmed the corresponding new test genuinely fails for the right
  reason, then restored and reconfirmed green."*
- `d363e79` (fail-closed sweep): *"Load-bearing verification: reverted
  the ibkr.py fix alone, confirmed
  `test_get_order_status_filled_with_a_genuine_zero_fill_stays_zero`
  fails with `assert None == 0.0` (the exact old bug), then restored the
  fix and reconfirmed green."*

A change with no such step, or with a test that was only ever run
against the fixed code, has not met this bar — see
`docs/process/REVIEW_CHECKLIST.md` and `docs/agents/VERIFICATION.md`.

## 5. Rebased cleanly onto the current shared branch tip

`git fetch origin claude/signal-copier-redesign && git rebase
origin/claude/signal-copier-redesign` with no unresolved conflicts,
including re-checking for an Alembic revision-number collision against
whatever landed on the branch since this work started (see
`docs/process/GIT.md`).

## 6. Re-verified after the rebase

Steps 1–4 are re-run **after** rebasing, not just before. A rebase can
silently reintroduce a conflict resolution mistake, or land on top of a
sibling's migration renumbering that this change's own migration now
needs to chain onto. Commit `cdfee8b` is a rebase-time fix of exactly
this kind (a fresh migration-number collision discovered only once two
concurrent branches were combined).

## 7. Pushed

`git push origin HEAD:claude/signal-copier-redesign`. A change that is
only committed locally, or sitting on an agent's own worktree branch and
never pushed to the shared branch, is not done — the next agent's rebase
cannot see it.
