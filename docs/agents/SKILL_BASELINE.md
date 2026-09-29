# Skill/Tooling Baseline — Signal Copier monorepo

Recorded 2026-09-29, on branch `claude/signal-copier-redesign` at commit `91dd746`.
This is Phase 1 of a Claude Code environment-efficiency initiative (skills, hooks,
MCP, commands, model routing). Facts below were verified directly against the
repository, not assumed. See `docs/agents/TOKEN_COST_AUDIT.md` for the waste
analysis this baseline feeds into.

## Repository shape

Monorepo at repo root containing three independently-versioned Python apps/packages:

- `signal-copier/` — private, SQLite-backed live-trading engine (14 broker adapters,
  12 signal-source adapters, FastAPI app, ~76 `app/` source files, 15 Alembic
  migrations, 1300+ tests).
- `signal-portfolio-commercial/` — multi-tenant, Postgres+RLS-backed commercial
  platform (67 service modules, 33 models, 37 Alembic migrations, 57 Jinja2
  templates, 869+ tests against a real disposable Postgres cluster).
- `signal_platform_contracts/` — shared, dependency-free event-contract package
  used by both apps' cross-app relay/export pipeline.

Plus an unrelated legacy app at repo root (`main.py`, `config.py`, `src/`,
`data_folder/`) — a LinkedIn job-application auto-applier ("AIHawk"). This is a
different product bolted onto the same GitHub repo; it has its own root
`ci.yml`/`integration-docker-build-ci.yml` workflows and is out of scope for
this trading-system efficiency initiative unless stated otherwise.

## CLAUDE.md

**No root-level `CLAUDE.md` exists.** The only `CLAUDE.md` files in the repo are
two nested, narrow-scope ones:
- `signal-portfolio-commercial/spec/CLAUDE.md`
- `signal-portfolio-commercial/dashboard_spec/CLAUDE.md`

Both predate this session's documentation-architecture wave and were not part of
it (that wave added slim, repo-root `CLAUDE.md` files to `signal-copier/` and
`signal-portfolio-commercial/` — those now exist; this baseline is about the
monorepo root, which still has none). No context-bloat problem exists yet at
the CLAUDE.md level simply because there is no root CLAUDE.md consuming context
on every turn — but see TOKEN_COST_AUDIT.md for where the equivalent
instruction-repetition is actually happening (in-prompt task descriptions).

## `.claude/` directory (repo root)

```
.claude/
├── agents/
│   ├── customer-ux-review.md
│   ├── financial-review.md
│   └── tenant-rights-review.md
├── skills/
│   ├── portfolio-commercial-build/SKILL.md
│   ├── portfolio-customer-site/SKILL.md
│   ├── portfolio-publishers/SKILL.md
│   ├── portfolio-release-audit/SKILL.md
│   ├── portfolio-research/SKILL.md
│   └── portfolio-rights-review/SKILL.md
├── scheduled_tasks.lock
└── worktrees/        (17 stale entries — see "Repo hygiene" below)
```

### Existing skills (6)

All six are `signal-portfolio-commercial`-specific (none for `signal-copier`,
none generic/cross-cutting). Convention observed (all follow this exactly):

```yaml
---
name: <kebab-case-name>
description: <one line>
disable-model-invocation: true
---
<single dense paragraph, imperative voice, no headers, no examples>
```

- `portfolio-commercial-build` — general build-forward-safely instruction for the
  commercial platform.
- `portfolio-customer-site` — customer-facing site build guidance.
- `portfolio-publishers` — Collective2/eToro/CopyFactory publisher work guidance,
  explicitly "no real trading, signal publishing... or automatic financial release."
- `portfolio-release-audit` — release-readiness audit guidance.
- `portfolio-research` — portfolio research/selection work guidance (shown in full
  above as the representative example).
- `portfolio-rights-review` — rights-registry/enforcement work guidance.

All six reference `ops/commercial_state.json` and `ops/COMMERCIAL_RESUME.md` as
the durable state/handoff mechanism — this predates and is narrower than the
`.agent/task-state.json` / `docs/state/*` layer this session's documentation
wave added; **these two state mechanisms are not yet reconciled** (see
TOKEN_COST_AUDIT.md finding on duplicate state tracking).

### Existing subagents (3)

Same terse convention, `tools:` frontmatter restricting each to read-only
(`Read, Glob, Grep`):

- `financial-review` — independent review of quantities/NAV/fees/timing/
  publication recovery, explicitly cannot approve financial release.
- `customer-ux-review` — independent customer-journey/claims/accessibility/
  disclosure review.
- `tenant-rights-review` — independent tenancy/source-rights/processor-mandate/
  secrets/authority-gate review.

These are exactly the "adversarial reviewer" pattern the broader initiative asks
for (Phase 6 `/adversarial-review`, `/second-opinion`) — already present, already
narrow-toolset, already well-scoped. **Do not duplicate these with a new
generic code-reviewer skill; extend or reference them instead.**

### `.claude/scheduled_tasks.lock`

Present but not inspected in this pass (out of scope for a static skill audit —
it's runtime lock state, not configuration).

### `.claude/worktrees/` — repo hygiene finding

17 worktree directories are checked into the live working tree under
`.claude/worktrees/agent-<id>/`, each a **full second copy of the entire
monorepo** (signal-copier/, signal-portfolio-commercial/, signal_platform_contracts/,
plus the legacy AIHawk app — `main.py`, `data_folder/`, etc.). Confirmed via
`git worktree list`:

| Worktree | Branch | Status |
|---|---|---|
| `agent-a0c6adc8c224c91cb` | `batchB-work` | stale, pre-dates current PR #4 branch |
| `agent-a1ae5019b9e07ff8f` | `worktree-agent-a1ae5019b9e07ff8f` | stale |
| `agent-a3c2bc6d3360d41e4` | `worktree-agent-a3c2bc6d3360d41e4` | stale |
| `agent-a770de3ec415c4390` | `phaseA2-work` | stale |
| `agent-a7f12d438a6797895` | `batchC-work` | stale |
| `agent-a8e3b1f1a323067e7` | `phaseB10-work` | stale |
| `agent-a8f0a728bac24a7c3` | `phaseB9-work` | stale |
| `agent-aa2da198334b16d67` | `phaseA3-work` | stale |
| `agent-aa4753fe43aaaed80` | `viz-work-b3` | stale |
| `agent-ab6516e138eeab571` | `phaseB4B5-work` | stale |
| `agent-ab73785f11ae2e898` | `phaseB8-work` | stale |
| `agent-abadbc5e346bd3971` | `phaseB7-work` | stale |
| `agent-ac19331af91000283` | `phaseA4-work` | stale |
| `agent-ad8c228950bdf253e` | `claude/signal-copier-safety-features` | stale, merged/superseded branch |
| `agent-ae0f89f706d681ef8` | `claude/signal-copier-visualization-data` | **this is PR #4's own base branch — do not delete without checking it's safe** |
| `agent-ae2d297199c397eaf` | `phaseA5-work` | stale |

This is real, measurable overhead: every one of these is a full repo checkout
sitting inside the primary working tree, which means any tool that does a
repo-wide file walk or glob without excluding `.claude/worktrees/` (grep, find,
a naive repository-map builder) pays an ~18x cost. This session has repeatedly
had to remember to add `-not -path "*/worktrees/*"` to searches. **This is a
concrete, high-priority token/latency waste finding — see TOKEN_COST_AUDIT.md.**
Recommended remediation: `git worktree remove` each stale one (after confirming
no uncommitted work), except the PR-#4-base-branch one, which should be checked
with the user first since worktrees can hold genuinely relevant historical
checkouts.

## Hooks

**None configured.** No `.claude/settings.json`, no `hooks/` directory, no
hook entries anywhere in the repo. Every deterministic check this session has
run (ruff, mypy, pytest, secret-scan) has been invoked manually inside each
audit/build agent's own prompt text, repeated near-verbatim across dozens of
agent dispatches this session. This is the single largest concrete
instruction-repetition source found — see TOKEN_COST_AUDIT.md.

## MCP servers (repo-level)

**No `.mcp.json` at repo root or in either app.** The Claude Code session this
conversation runs in does have various MCP servers available (GitHub, Supabase,
Vercel, etc. per the session's system configuration), but none of that is
repo-declared — it's host/session-level, not something a fresh clone or a
different developer's session would automatically get. No repo-level MCP
configuration exists to audit for this initiative's "MCP optimization" phase.

## Slash commands / custom commands

**None.** No `.claude/commands/` directory. All work this session has been
driven by long, explicit prose instructions per-agent-dispatch rather than
reusable slash commands — this is the second-largest instruction-repetition
source (see TOKEN_COST_AUDIT.md). The one exception: `.claude/skills/autopilot/
SKILL.md` (added by this session's documentation-architecture wave) is a
16-step `/autopilot <goal>` workflow skill, but it's the only reusable
multi-step command that exists.

## Scripts / task runner

**No Makefile, no justfile, no package.json scripts.** Every "run the tests",
"run ruff", "run mypy" instruction across this entire session (dozens of times,
across dozens of dispatched agents) has been a raw shell command spelled out in
full in the agent's prompt, e.g.:

```
cd signal-copier && python -m pytest tests/ -q -k "engine or routing or risk..."
```

repeated with slight variations by nearly every agent this session has
dispatched. This is a clean, high-value, low-risk target for a `Makefile` per
app (`make lint`, `make typecheck`, `make test`, `make verify`) — see
TOKEN_COST_AUDIT.md and the Phase 5 recommendation.

## CI/CD (GitHub Actions)

`.github/workflows/`:
- `signal-copier-ci.yml` — ruff, CI-scoped mypy (31 hardcoded files via
  `--follow-imports=silent`), pytest, pip-audit (with one documented CVE
  exception), gitleaks secret-scan, mutation-test note for `app/auth.py`.
- `signal-portfolio-commercial-ci.yml` — ruff (`select=["F","B"]`), mypy
  (`app --ignore-missing-imports`, i.e. **not** CI-scoped like signal-copier's —
  the whole app is checked), pytest against a real disposable Postgres 16
  cluster spun up by `tests/conftest.py`, a separate `alembic upgrade head`
  verification step against a second disposable cluster.
- `ci.yml`, `integration-docker-build-ci.yml`, `stale.yml` — belong to the
  unrelated legacy AIHawk app / repo-wide bot housekeeping, out of scope here.
- `dependabot.yml` present at `.github/` — automated dependency-update PRs
  already configured at the GitHub level (separate from the in-CI `pip-audit`
  step, which catches known CVEs in the currently-pinned versions rather than
  proposing upgrades).

**Finding**: `signal-copier`'s CI-scoped mypy check (31 specific files) is
narrower than `signal-portfolio-commercial`'s (whole `app/` tree). This
session's Phase-1-adjacent audit work (today, separate from this doc) ran full
non-scoped mypy on both and found 18 real type errors in signal-copier across
8 files that the CI-scoped check misses entirely, vs. zero for
signal-portfolio-commercial's already-full check. This is a genuine CI gap,
not just a token-efficiency one — tracked as a finding for the code audit, but
worth noting here since "CI scope narrower than the codebase" is exactly the
kind of technical debt a `/repo-health` or `quality-gates.yaml` initiative
should catch going forward.

## Test frameworks / lint / type-check / security tooling (both apps)

Both apps use the same toolchain (already fully wired into CI, confirmed
working this session via multiple fresh-clone verification runs):

- **pytest** — signal-copier: SQLite-backed, 1313 passed / 8 skipped as of the
  current head. signal-portfolio-commercial: Postgres-backed via a real
  disposable cluster per test session, 869+ passed.
- **ruff** — lint, both apps, clean.
- **mypy** — type-check; CI-scoped (31 files) for signal-copier, full-tree for
  signal-portfolio-commercial.
- **pip-audit** — dependency vulnerability scan, both apps, one documented
  exception (CVE-2026-49265 / oauthlib, no upstream fix available, tweepy
  hard-pins `<4`).
- **gitleaks** — secret scanning, signal-copier CI (not yet confirmed present
  in signal-portfolio-commercial's CI — worth checking in a follow-up pass).
- **Hypothesis** — property-based/stateful testing present for at least
  quantity-conservation invariants (signal-copier).
- **Schemathesis** — API fuzzing against the FastAPI OpenAPI schema
  (signal-copier).
- **Playwright + axe-core** — accessibility checks in CI (signal-copier).
- **Toxiproxy-style fault injection** — present in signal-copier's test suite
  per this session's earlier work (C32).
- No coverage.py / coverage reporting wired into CI for either app as of this
  baseline (coverage was run ad-hoc by a same-day audit agent, not as a
  standing CI gate) — a real gap for the "code coverage" baseline category.

## Documentation / state layer

As of this session's documentation-architecture wave (landed earlier the same
day as this baseline), both apps now have:

- `CLAUDE.md` (slim operating contract, app-level, not repo-root)
- `docs/` (~75 files each): architecture, design, adr (8 each), database,
  security, standards, testing, operations, observability, integrations,
  process, agents, state, history, plus root-level ROADMAP/FEATURES/
  KNOWN_ISSUES/TECH_DEBT/ASSUMPTIONS/GLOSSARY/REPO_MAP/CHANGELOG.
- `docs/manifest.yaml` — machine-readable doc index with `read_when`/
  `update_when` triggers per category (verified complete against the real
  tree as of this baseline — zero missing/dangling entries).
- `.agent/autonomy.yaml` — 5-level (0-4) autonomy policy, grounded in each
  app's real risk surface.
- `.agent/context-map.yaml` — trigger-based map from task area to docs to read
  first.
- `PROJECT_STATUS.yaml` — machine-readable health/test-count snapshot.

**Not yet present** (relevant to this initiative's later phases):
- `docs/INDEX.md` (the manifest.yaml partially covers this role already)
- `.agent/task-state.json`, `.agent/verification-state.json` (structured,
  machine-writable — the current state docs are all Markdown/YAML narrative,
  not a format a script could cheaply diff/update)
- `.agent/quality-gates.yaml`
- `.agent/model-routing.yaml`
- `.agent/rules.yaml`, `.agent/workflows.yaml`, `.agent/permissions.yaml`
  (referenced in this session's earlier documentation-architecture planning
  but not actually built — only `autonomy.yaml` and `context-map.yaml` landed)
- No `docs/api-specifications/` or formal OpenAPI spec file beyond whatever
  FastAPI auto-generates at runtime (Schemathesis tests against the live
  schema, but there's no checked-in static spec for offline reference)

## Skills/commands/workflows relevant to THIS initiative already present

- `.claude/skills/autopilot/SKILL.md` — the one existing reusable multi-step
  workflow skill (16-step `/autopilot <goal>`), monorepo-root-scoped, aware of
  both apps' real CI commands and this session's worktree-dispatch pattern.
  This is a genuine, already-working instance of exactly what Phase 8
  ("Workflow Composition") of this initiative asks for — treat it as the
  template/precedent, not something to replace.

## Summary: what this initiative is actually starting from

Strong: real CI, real tests, real docs/state layer just built, an existing
skill/subagent convention already established and followed consistently, one
working top-level workflow skill (`/autopilot`).

Weak/missing: no hooks (100% of deterministic checks are manually re-typed into
every agent prompt), no Makefile (same problem, different angle), no
repo-level MCP config, no slash commands beyond `/autopilot`, no
`quality-gates.yaml`/`model-routing.yaml`/`task-state.json`/
`verification-state.json`, 17 stale worktrees bloating every unscoped repo
search, two parallel and un-reconciled state-tracking conventions
(`ops/commercial_state.json` from the pre-existing skills vs. this session's
`docs/state/*`).
