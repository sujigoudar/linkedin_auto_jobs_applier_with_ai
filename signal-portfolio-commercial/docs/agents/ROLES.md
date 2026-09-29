# Roles

This app has never had specialized long-lived agent roles (a
"reviewer agent," a "migration agent," etc.) — every session in its
history is a single general-purpose Claude Code session doing the full
loop: read, implement, test, verify, document, rebase, push. What
varies is the *shape* of the task each session picks up, evidenced by
the commit history itself.

## The shapes of work this app's history actually shows

- **Feature-slice sessions** — the bulk of the history: one CU-/AD-/PU-/
  INT- item at a time (e.g. `e303af9` ID-01/ID-02/ID-03 auth, `64d596d`
  CU-06 drawdown/win-rate, `f752904` AD-18 evidence manifest). Each
  picks up exactly one named requirement from the spec/acceptance
  documents, builds the real deterministic part, discloses what it
  didn't build, and closes with load-bearing verification.
- **Cross-cutting/integration sessions** — touch both
  `signal-copier` and `signal-portfolio-commercial` in one commit
  because the feature genuinely spans both processes (e.g. `9ddc681`
  dual-secret rotation, `d6613bb` wiring `signal-copier`'s real `.env`
  into `docker-compose.yml`). These stay in one worktree covering both
  paths for that single unit of work, never two separate uncoordinated
  sessions editing the shared contract at once.
- **CI/infrastructure-fix sessions** — narrowly scoped, e.g. `43e1ac0`
  "Fix CI smoke test: read owner token from a file, not masked logs,"
  `2c1079a` "Fix two more real deployment gaps found by actually
  running containers." These exist because the prior session's own
  claimed-done work didn't survive contact with a real CI runner or a
  real `docker compose up` — the fix session's job is specifically to
  make the previously-claimed state actually true, not to add new
  scope.
- **Audit/reconciliation sessions** — e.g. `1945c2e`
  "Acceptance-case verification pass against the integration pack's
  own 40 cases," `77a6fef` "Re-verify rights registry and command
  authority against new integration boundaries." These re-check
  standing guarantees (rights checks, command authority, RLS
  boundaries) against everything that has been added *since* they were
  last verified, rather than assuming a guarantee proven once stays
  true forever as the codebase grows.
- **Regression-fix sessions** — e.g. `64d596d`, which found and fixed a
  bug a prior session's own load-bearing test had failed to catch, and
  strengthened the test suite so the same bug class can't recur
  silently.

## What every session is responsible for, regardless of shape

1. Reading enough of the real current code and state (see `HANDBOOK.md`,
   `ROUTING.md`) to avoid re-implementing something that already
   exists, or contradicting a boundary a prior session deliberately
   built (RLS, `relay_role`'s restricted grants, the append-only
   ledger, fail-closed permission checks).
2. Building only what can be built for real in this environment —
   disclosing, never faking, what a missing external dependency (a
   broker sandbox, licensed market data, a live payment processor)
   blocks (see `docs/KNOWN_ISSUES.md`, `docs/PENDING_DECISIONS.md`).
3. Proving the change with load-bearing verification (`docs/process/DEFINITION_OF_DONE.md`).
4. Leaving an honest trail: a commit message a cold reader can use
   without re-reading the diff, and updated state docs when the change
   is significant enough to shift `docs/state/PROGRESS.md` or the
   acceptance status.
5. Rebasing onto the shared branch and re-verifying against the
   rebased tree before pushing (`docs/process/GIT.md`).

There is no separate "reviewer" role distinct from "author" in this
app's history — review happens as a later session re-reading and
re-verifying, per `REVIEW_CHECKLIST.md`, not as a synchronous
human-in-the-loop gate before merge. Treat every session's own final
pass over its own diff as that review, using the same checklist.
