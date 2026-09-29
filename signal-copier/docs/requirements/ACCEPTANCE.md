# Acceptance Criteria

What "done" looks like for a change in `signal-copier/`, derived
directly from `.github/workflows/signal-copier-ci.yml` (the actual gate
every push/PR runs through) plus this codebase's own load-bearing
conventions.

## 1. Lint clean

```
ruff check .
```

run from `signal-copier/` (the CI job's `working-directory`). The
ruleset is scoped by `pyproject.toml`, not the ruff default set —
check that file for the real, current rule selection before assuming
a rule applies or doesn't.

## 2. CI-scoped mypy clean

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

This is a **fixed, explicit file list**, not `mypy app/` or `mypy .` —
mypy runs only over these named modules (plus whatever they import,
per `--follow-imports=silent`). A file not on this list can carry type
errors without failing CI; a change that touches one of these files
must not introduce a new error CI would catch. A change that adds a
new module this codebase wants type-checked going forward should add
it to this exact list in `.github/workflows/signal-copier-ci.yml`, not
assume it's covered implicitly.

## 3. Full pytest suite green

```
pytest -q
```

run from `signal-copier/`, against the full test tree (see
`pytest.ini`) — not a subset. A new behavioral change needs a real
test exercising it, matching this codebase's own pervasive convention
of load-bearing tests over docstring claims alone.

## 4. A real, load-bearing verification performed

Every non-trivial mechanism in this codebase is documented as having
been verified by a real, concrete check at the time it was built —
never merely asserted. The acceptance bar for a change here follows
the same pattern: a change is not "done" because it compiles and
passes existing tests unmodified — it needs a demonstration specific
to what changed, for example:

- A schema change: confirm the new/changed column round-trips through
  a real `SignalStore` call (not just that `CREATE TABLE`/`ALTER
  TABLE` succeeds), and that `docs/database/SCHEMA.md`/
  `DATA_DICTIONARY.md`/`MIGRATIONS.md` are updated to match (see
  `docs/database/MIGRATIONS.md`'s "Practical rule for the next schema
  change").
- A new Alembic revision: confirm `alembic upgrade head` against a
  genuinely empty database produces the same schema `SignalStore`'s
  own bootstrap does (the same guarantee `0001`'s own existence
  depends on), and that `docs/database/MIGRATIONS.md`'s head-revision
  table is updated.
- A change to a fail-closed gate (capital allocator, writer lease,
  command ledger classification): a test that specifically exercises
  the ambiguous/unresolved case and asserts it fails closed, not just
  the happy path.
- A change to `app/lifecycle/manager.py`'s exit sequencing: a test
  that specifically exercises the partial-fill/oversell-prevention
  path (`_compute_reduction_plan`, the stop-resize transition), since
  that is the exact failure mode the subsystem exists to prevent.

## 5. Dependency and secret hygiene

- `pip-audit -r requirements.txt` must be clean, modulo the one
  explicitly dated, justified exception already present in the CI
  workflow (see that file's own comment for the current exception and
  why it can't yet be closed). A new vulnerability requires either a
  real fix (a version bump) or the same kind of dated, justified,
  narrowly-scoped `--ignore-vuln` — never a blanket suppression.
- `gitleaks` (the dedicated `secret-scan` CI job) must find nothing new
  in the diff. No committed credentials, API keys, or tokens, ever —
  including in test fixtures or example config.

## 6. No fabricated behavior in documentation

Documentation changes (this directory, `docs/adr/`, `docs/design/`,
`docs/database/`) are held to the same standard as code: every claim
must be traceable to a real source-code fact — a specific comment,
docstring, table, or test — never an inferred or aspirational
description of what a subsystem "should" do. A documentation PR that
describes behavior not actually present in the code it cites is not
acceptable, symmetrically with a code PR that claims a guarantee
(e.g. "fails closed") it doesn't actually implement.

## Summary checklist for a change in `signal-copier/`

- [ ] `ruff check .` clean
- [ ] CI-scoped `mypy` invocation clean (see the exact command above)
- [ ] `pytest -q` fully green
- [ ] A real, specific verification of the changed behavior performed
      and, where applicable, captured as a new/updated test
- [ ] `pip-audit`/`gitleaks` clean (or a justified, dated exception)
- [ ] Any schema/migration change updates
      `docs/database/SCHEMA.md`/`DATA_DICTIONARY.md`/`MIGRATIONS.md`
      to match
- [ ] Any new architectural decision is recorded as an ADR
      (`docs/adr/`) rather than left implicit in a commit message
