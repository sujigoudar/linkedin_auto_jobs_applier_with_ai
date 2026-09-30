# Verification — the load-bearing bar

This is the single most consistent discipline across this app's entire
commit history, restated here because it's the thing most worth an
agent internalizing before touching this codebase: **a test that has
never been observed to fail is not verified.**

## The actual loop, every time

1. Write (or identify) the assertion that should catch the bug class
   the change guards against.
2. **Break the real guarantee on purpose** — comment out the check,
   revert the fix, disable the gate, swap in a stale formula.
3. Run the test(s) and confirm the *specific, predicted* assertion
   fails — not just "something failed," but the expected failure for
   the expected reason.
4. Restore the real code.
5. Re-run and confirm green again.
6. Say all of this happened, specifically, in the commit message.

## Real examples straight from this app's history

- **`64d596d`** (CU-06 drawdown): "broke the fix back to 'peak =
  previous point', confirmed only the new test failed with the exact
  predicted wrong numbers (100/2min instead of 150/4min), restored,
  reconfirmed all 7 tests green."
- **`3b8cf40`** (INT-033 single-writer enforcement): "temporarily
  disabling the rejection branch and confirming exactly the two tests
  exercising it fail, then restoring."
- **`72efeae`** (revocable JWT sessions): "Manually confirmed these
  tests fail when `verify_token` is changed to skip the denylist
  check, and pass again once restored."
- **`e303af9`-adjacent style, general pattern**: fail-closed checks
  (rights, permissions, RLS) are proven by a temporarily-broken-then-
  restored test per `ops/COMMERCIAL_RESUME.md`'s own standing
  convention, never "an assertion that reads correctly" alone.

## Why this matters more than test count

A test suite can be large and still prove nothing if every assertion
happens to pass regardless of whether the guarded behavior is present.
This app's own history shows this isn't hypothetical: `64d596d`'s
existing load-bearing test for drawdown *did not* catch a real
peak-tracking regression, because that test's own true peak happened
to sit immediately before its own true trough — a coincidence of test
data, not a property of the assertion. The fix wasn't just patching the
bug; it was adding
`test_max_drawdown_uses_the_true_running_peak_not_just_the_previous_point`,
a test specifically shaped to catch that bug class, and then
re-verifying it the same way (break, fail, restore, pass).

## Two independent verification layers, not one

- **Application-level tests** (`pytest`, against a real disposable
  Postgres cluster) prove the models, services, and route handlers
  behave correctly.
- **Migration-chain verification** (`alembic upgrade head` against a
  *separate* fresh database, a dedicated CI step) proves the actual
  deployment path — the DDL a real production database will run —
  works, which the test suite's own `Base.metadata.create_all` setup
  does not exercise at all. `04c418cbb547`'s own docstring documents a
  real bug this layer caught that the test suite alone did not: a
  fresh migration replay failing with `UndefinedTable` because a
  migration was (incorrectly) referencing the live, ever-growing table
  list instead of a frozen historical snapshot.

Both layers are required. A change is not verified with only one.

## Honesty as part of verification

Verification also means not claiming something works when a real
dependency this environment doesn't have (a broker sandbox, live
vendor API, payment processor account) makes it impossible to actually
prove. This codebase's convention — visible in `collective2_publisher.py`,
`etoro_adapter.py`, `stripe_webhook.py`, `mam_allocation.py`,
`pamm_accounting.py`, and dozens of commit messages — is to build and
verify the deterministic part that *can* be proven for real, and mark
the rest `PARTIAL`/`BLOCKED`, citing exactly what evidence is missing,
rather than simulate a fake pass. See `docs/KNOWN_ISSUES.md` and
`docs/ASSUMPTIONS.md`.
