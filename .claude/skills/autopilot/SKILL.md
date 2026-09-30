---
name: autopilot
description: 16-step autonomous development workflow (RESUME through REPORT) for either app in this monorepo. Use for /autopilot <goal> when the user wants a feature, bug fix, refactor, migration, integration, UI change, security fix, or release handled end-to-end with minimal back-and-forth.
disable-model-invocation: true
---

# Autopilot: 16-step autonomous development workflow

Invoked as `/autopilot <goal>`. This skill is shared at the monorepo root and
applies to both `signal-copier/` (private trading engine) and
`signal-portfolio-commercial/` (multi-tenant commercial platform), which
share `signal_platform_contracts/`. Pick the target app from the goal text
before step 1; if genuinely ambiguous (the goal could plausibly touch
either app, or spans both), ask the user which app once at the start rather
than guessing.

**Authoring-time note on dependencies:** this skill was authored while
several other background agents were concurrently adding, per app,
`docs/`, `.agent/rules.yaml`, `.agent/workflows.yaml`,
`.agent/quality-gates.yaml`, `.agent/permissions.yaml`,
`.agent/context-map.yaml`, `.agent/autonomy.yaml`, `PROJECT_STATUS.yaml`,
and a slim `CLAUDE.md`. At authoring time, `git log` showed real `docs/`
commits landed for both apps, but **no `<app>/.agent/` directory and no
`<app>/PROJECT_STATUS.yaml` existed yet in this branch's history** — those
are referenced below as the intended, not-yet-confirmed layout. Every step
that reads one of these files MUST first check the file actually exists
(e.g. `test -f <app>/PROJECT_STATUS.yaml`, `test -f <app>/.agent/context-map.yaml`)
and degrade gracefully (see each step's fallback) rather than fail or
fabricate content, in case a given file still hasn't landed by the time
this skill runs.

Do not touch `<app>/.agent/`, `<app>/docs/`, `<app>/PROJECT_STATUS.yaml`,
or `<app>/CLAUDE.md` from within this skill except to *read* them and, in
step 13/15, to append/update `PROJECT_STATUS.yaml` and `docs/` entries
that are the natural output of the task itself — never restructure or
regenerate those files wholesale; that is other agents' territory.

For each step below, `<app>` means `signal-copier` or
`signal-portfolio-commercial`, whichever the goal concerns.

---

## 1. RESUME

- `test -f <app>/PROJECT_STATUS.yaml && cat <app>/PROJECT_STATUS.yaml` — read
  current status, in-flight work, known risks. If it doesn't exist yet,
  say so explicitly and continue without it (do not invent one here; that
  file's shape belongs to the sibling agent building it).
- `git -C <app> log --oneline -20` and `git status` (repo root) — check for
  uncommitted work, stray branches, or a worktree left over from an
  interrupted run (`git worktree list`).
- If there's evidence of interrupted work matching this goal (an
  `agent-<id>` branch/worktree with related commits, an unmerged branch),
  resume it explicitly rather than starting over: check out that
  worktree, re-read what it already did, and continue from there.

## 2. CLASSIFY

Classify the goal into exactly one of: feature / bug / refactor /
migration / integration / UI / security / release. State the
classification explicitly — it drives which context gets loaded (step 3)
and which autonomy rules apply (step 5). If it spans two categories (e.g.
"fix a bug in the migration"), pick the dominant one and note the
secondary.

## 3. LOAD CONTEXT

- `test -f <app>/.agent/context-map.yaml && cat <app>/.agent/context-map.yaml`.
  If present, use it to find the specific `docs/` files (and any code
  entry points it names) mapped to this step's classification/area —
  load only those, not the whole `<app>/docs/` tree.
- If `context-map.yaml` doesn't exist yet, fall back to a targeted guess
  from the classification against the real docs directories that do
  exist today, e.g. for signal-copier: `docs/security/`, `docs/operations/`,
  `docs/observability/`, `docs/integrations/`, `docs/FAILOVER.md`,
  `docs/TROUBLESHOOTING.md`; for signal-portfolio-commercial:
  `docs/standards/`, `docs/testing/`, `docs/00_discovery.md`,
  `docs/12_validation_report.md`. Grep filenames for the classification's
  keywords before opening whole directories.
- Also skim `<app>/CLAUDE.md` if present for repo-specific conventions.

## 4. RESEARCH

Ground the plan in the actual code, not assumptions:

- `grep`/`rg` in `<app>/app/` (and `<app>/tests/`) for existing
  implementation related to the goal — names, endpoints, models.
- `git -C <app> log --oneline --all -- <relevant path>` and
  `git blame` on the files most likely to change, to understand recent
  intent and avoid re-litigating settled decisions.
- Check `<app>/requirements.txt` / `pyproject.toml` for what's already a
  dependency before proposing a new one.
- For signal-copier work touching broker/source adapters, check
  `signal_platform_contracts/` for the shared contract shape before
  changing anything that crosses the app boundary.

## 5. GRILL-AUTONOMOUS

- `test -f <app>/.agent/autonomy.yaml && cat <app>/.agent/autonomy.yaml`.
  Use its levels to resolve as much as possible without asking:
  - **Level 1/2** decisions (per that file's own definitions — typically
    reversible, low-blast-radius, within established patterns): decide
    and record the decision + rationale (goes into step 13's docs update
    and step 16's report). Do not ask the user about these.
  - **Level 3/4** decisions (irreversible, cross-boundary, financial/
    security/customer-data-affecting, or outside established patterns):
    surface these to the user via `AskUserQuestion`, batched into as few
    prompts as practical, before proceeding to SPECIFY.
- If `autonomy.yaml` doesn't exist yet, fall back to conservative
  judgment: treat anything touching money movement, broker order
  submission, auth/session logic, multi-tenant RLS/isolation boundaries,
  secrets, or a schema migration as needing a user check-in; treat
  everything else (internal refactors, test additions, doc updates, UI
  copy, non-breaking additive endpoints) as autonomous. Say explicitly in
  the report that this fallback was used.

## 6. SPECIFY

Write down, before touching code:

- Explicit requirements (what must be true when done).
- Acceptance criteria (how it will be verified — map forward to step 10).
- Invariants that must not break (e.g. for signal-copier: fail-closed
  sizing, exactly-once order submission, writer-lease exclusivity; for
  signal-portfolio-commercial: tenant RLS isolation, owner-only action
  gating, append-only ledgers). Pull these from the docs loaded in step 3
  rather than guessing.

## 7. DESIGN

- List real alternatives considered and why the chosen one wins (not a
  strawman).
- Sketch the architecture/data-flow change at the level of which
  files/modules are touched.
- State blast radius: what breaks if this is wrong, what's the smallest
  reversible unit, does it touch a migration or shared contract
  (`signal_platform_contracts/`) that both apps depend on.
- If touching a migration, an external integration, or anything hard to
  undo: write an explicit rollback plan before EXECUTE.

## 8. PLAN

- Break the design into atomic tasks (each independently reviewable and,
  ideally, independently testable).
- Identify which tasks are genuinely independent and can run in
  parallel. For parallel work, use the pattern already proven in this
  session:
  ```
  git fetch origin <base-branch>
  git worktree add /tmp/wt-<task-id> origin/<base-branch> -b agent-<task-id>
  ```
  then dispatch each via the Agent/Task tool into its own worktree.
  Rebase (never merge) each onto the base branch before pushing; never
  force-push a shared branch. Sequence tasks that touch the same files or
  have a real dependency instead of parallelizing them.

## 9. EXECUTE

Implement each planned task, directly or via the dispatched sub-agents
from step 8. Keep commits scoped to one atomic task where practical
(makes step 11's review and any later revert easier). Follow the coding
standards already documented for the app (e.g.
`signal-portfolio-commercial/docs/standards/CODING.md`,
`NAMING.md`, `ERROR_HANDLING.md`, `LOGGING.md` where they exist) rather
than introducing a new style.

## 10. VERIFY

Run the app's **real** checks — do not invent tooling that doesn't exist
in the repo. As of authoring time, neither app has a `Makefile`, so shell
out to the underlying commands directly (adding a `make verify` target
later is a reasonable follow-up, but is a TODO, not something to assume
exists — check `test -f <app>/Makefile` first in case it has landed since).

**signal-copier** (from `signal-copier/`):
```
ruff check .
python -m mypy app/db.py app/engine.py app/reconciliation.py app/lifecycle/manager.py \
     app/lifecycle/models.py app/routing.py app/risk.py app/models.py app/main.py \
     app/backtest/simulator.py app/sources/webhook.py app/sources/whatsapp.py \
     app/sources/sms_twilio.py app/sources/ninjatrader.py app/sources/text_parser.py \
     app/sources/base.py app/brokers/alpaca.py app/brokers/base.py app/brokers/paper.py \
     app/brokers/ccxt_broker.py app/brokers/signalstack.py app/brokers/ninjatrader.py \
     app/config.py app/auth.py app/rate_limit.py app/context/sec_edgar.py app/context/fred.py \
     app/context/fx.py app/capital_allocator.py app/provider_value.py app/provider_scout.py \
     --follow-imports=silent
pytest -q
```
(mirrors `.github/workflows/signal-copier-ci.yml` exactly; the mypy file
list there is the authoritative source if the two ever diverge — re-read
the workflow file, don't just trust this copy). If the change touches
`app/auth.py`, also consider running the scoped mutation suite
(`mutmut run`, see `[tool.mutmut]` in `signal-copier/pyproject.toml`) —
optional but strongly indicated for auth changes.

**signal-portfolio-commercial** (from `signal-portfolio-commercial/`):
```
ruff check .
mypy app --ignore-missing-imports
pytest -q
```
`pytest -q` here runs against a real, disposable local PostgreSQL cluster
that `tests/conftest.py` starts per test session (see that file's own
docstring) — it needs `postgresql-16` server binaries at
`/usr/lib/postgresql/16/bin` and will `pytest.skip` (not fail) the
Postgres-dependent tests if that binary isn't present; if it skips
unexpectedly, that's an environment gap to flag in step 16, not a pass.
If the change includes a migration, also verify it applies cleanly the
way CI does (`.github/workflows/signal-portfolio-commercial-ci.yml`'s
"Verify Alembic migrations apply cleanly" step): spin up one more
disposable cluster and run `alembic upgrade head` against it.

For either app, re-read the actual workflow file
(`.github/workflows/signal-copier-ci.yml` /
`.github/workflows/signal-portfolio-commercial-ci.yml`) before relying on
this skill's copy of the commands — CI is the source of truth and may
have changed since this was written.

## 11. ATTACK

Adopt an adversarial-reviewer stance, not the implementer's. Explicitly
look for:

- Spec drift — does the diff actually match step 6's acceptance criteria,
  or did scope quietly shrink/shift during EXECUTE?
- Shortcuts — TODOs left in, error paths swallowed, tests weakened to
  pass rather than the code fixed.
- Untested edge cases — empty input, concurrent access, partial failure,
  the specific invariants named in step 6.
- Security issues — new auth bypass, tenant-isolation leak, secret
  logged, injection, SSRF via a new outbound call.

Treat any finding here the same as a VERIFY failure: it feeds step 12.

## 12. SELF-HEAL

If VERIFY or ATTACK found real problems: fix them, then re-run the
relevant parts of VERIFY (and re-attack the fixed area) until clean. Loop
rather than declaring done on the first pass. Only break out to ask the
user if:
- a fix requires a Level 3/4 decision (per step 5's rubric), or
- the problem can't be safely resolved autonomously (e.g. the fix
  requires a design change with real blast radius, or reveals the
  original SPECIFY was wrong in a way that changes scope).

## 13. DOCUMENT

Update, as the change actually warrants:

- The relevant existing `<app>/docs/` file(s) touched by this change
  (don't create new doc files or directories outside what's already
  established without a clear reason — other agents own that structure).
- `<app>/PROJECT_STATUS.yaml` if it exists (append/update the entry for
  this piece of work — status, what changed, any new risk). If it
  doesn't exist yet, note in step 16 that this update was skipped and
  should be applied once the file lands.
- An ADR (or equivalent decision record, wherever this repo already
  keeps them, e.g. alongside `signal-portfolio-commercial/docs/00_discovery.md`-style
  numbered docs) only if an architecturally significant decision was
  made in step 7 — not for routine changes.

## 14. FINAL GATE

Run the **full** real verification suite one more time, clean, before
proceeding — this is the CI-equivalent gate, not a spot check:

- signal-copier: `ruff check .` + the full mypy file list above + full
  `pytest -q` (not just the tests for the changed area).
- signal-portfolio-commercial: `ruff check .` + `mypy app --ignore-missing-imports`
  + full `pytest -q` against the real disposable Postgres cluster (not
  skipped).

Do not proceed to CHECKPOINT while this is red. If something here can't
be made clean autonomously, stop and report it (step 16) rather than
committing broken state.

## 15. CHECKPOINT

- Commit with a clear, scoped message following this repo's existing
  convention (see recent `git log` — imperative summary line, body
  explaining what/why, evidenced against real files/tests, ending with
  this session's attribution trailer):
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: <this session's claude.ai/code/session_... URL>
  ```
  Never force-push. If working in a worktree/branch from step 8, rebase
  onto the current base branch before pushing and resolve conflicts by
  re-reading both sides, not by blindly taking one.
- Update `<app>/PROJECT_STATUS.yaml` (if present) to reflect the
  completed checkpoint.

## 16. REPORT

Summarize for the user:

- What changed (files/modules, one line each if many).
- Decisions made autonomously, each tagged with its autonomy level (from
  step 5) and one-line rationale.
- Verification evidence: actual test counts and pass/fail from step 14's
  run (not step 10's earlier partial run), lint/type-check results,
  migration check result if applicable.
- Remaining risks or known gaps (including any environment gaps hit,
  e.g. a skipped Postgres-dependent test suite).
- Anything that needed, or still needs, a user decision (the Level 3/4
  items from step 5, and anything step 12 couldn't self-heal).
