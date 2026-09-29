# Development lifecycle

The real sequence this app's commit history follows, session after
session. Skipping a step is how this codebase's own real bugs got in
(see the drawdown peak-tracking regression `64d596d` fixed, or the
`release_reviews` migration bug `04c418cbb547` fixed) — this is not a
formality.

## 1. Understand current behavior first

Before writing anything, read the real code the change touches, not
just the spec. This app's own convention (visible throughout its
history) is to reuse an existing computation rather than
re-implementing it:

- `64d596d` explicitly ports `signal-copier/app/statistics.py`'s own
  `compute_max_drawdown` rather than writing a second drawdown
  algorithm, and refactors `platform_performance.py`'s replay into
  `load_ordered_root_entries`/`apply_entry` so the new equity-series
  builder replays the *exact same* query and cost-basis math the
  existing `compute_book_performance` already uses.
- `a26f9174` (B7 capital-sharing backtest) reuses
  `app/capital_allocator.py`'s own `CapitalAllocator.admit()` — "the
  exact gate live trading calls before submitting an order" — instead
  of writing a parallel sizing check for backtests.
- `5e8d9e4`'s preview endpoints extract the exact pure planning
  functions `app/lifecycle/manager.py` already uses, "so a preview can
  never drift from the real computation."

Grep for the concept before assuming it doesn't exist. A second,
subtly-different implementation of something real code already
computes is a bug waiting to diverge, not a shortcut.

## 2. Implement

Follow the schema/contract that already exists rather than inventing a
new shape: `ops/COMMERCIAL_RESUME.md`'s own standing convention is "New
tables/enums/fields are copied field-for-field from
`spec/contracts/*.schema.json`, not redesigned." Keep the same
honesty discipline the rest of the codebase uses: when a real
dependency (a broker sandbox, a live vendor API, a payment processor
account) does not exist in this environment, build the deterministic
half that can be built for real and disclose the rest as an explicit
gap — never fabricate data or a fake integration to make a feature
"look" finished (see `KNOWN_ISSUES.md` for the running list this
produces).

## 3. Test against a real Postgres cluster

Never SQLite, never a mocked session — `tests/conftest.py` starts a
real disposable `postgresql-16` cluster per test session specifically
because this app's row-level security, append-only triggers, and
`relay_role` restricted-grant boundary are real Postgres features with
no SQLite equivalent; a mock would prove nothing about them. Run:

```
cd signal-portfolio-commercial
pip install -r requirements.txt
pytest -q
```

If `initdb` fails with a permission error, check that the `postgres`
system user can traverse every ancestor directory of pytest's tmp dir
— `tests/conftest.py`'s `postgres_cluster` fixture chmods ancestors to
`0711` to work around exactly this, a real issue hit during
development.

## 4. Verify migrations apply cleanly

Any change that adds or edits a model touching a persisted table needs
a real Alembic revision (`alembic revision --autogenerate -m "..."`,
then hand-check the generated `upgrade()`/`downgrade()` — autogenerate
does not know about RLS policies, append-only triggers, or
`relay_role` grants; those are hand-written, see e.g.
`3f7a19c02b8e_add_relay_role_access.py`). Before considering the change
done, run the same check CI's dedicated migration step runs: a fresh
disposable Postgres cluster, `alembic upgrade head`, confirm it applies
without error. See `DEFINITION_OF_DONE.md` for exactly why this is a
separate step from the test suite.

## 5. Load-bearing verification

Break the specific thing the change is supposed to guarantee, confirm
the new test fails for the predicted reason, restore, reconfirm green.
See `DEFINITION_OF_DONE.md` for real examples from this app's own
history.

## 6. Rebase, then push

```
git fetch origin claude/signal-copier-redesign
git rebase origin/claude/signal-copier-redesign
```

Resolve conflicts (including Alembic revision collisions — see
`GIT.md`), re-run the full verification loop (tests + migration check)
against the rebased tree, not just the pre-rebase diff, then:

```
git push origin HEAD:claude/signal-copier-redesign
```

## Standing constraints that apply to every change

From `ops/COMMERCIAL_RESUME.md`'s own "Conventions to keep following,"
still true:

- Every service function that gates a real action (rights, capital
  allocation, publication eligibility, revocation) must fail closed,
  proven by a temporarily-broken-then-restored test, never just an
  assertion that merely reads correctly.
- This service is Postgres-only. Never add a SQLite fallback or a mock
  session for its tests.
- Nothing here goes live (a real Stripe charge, a real Collective2/eToro
  publish, a real broker-managed account) without stopping and
  surfacing that explicitly first — see `docs/PENDING_DECISIONS.md`
  and `RELEASE.md` for the standing owner-only action cards this gates
  on.
