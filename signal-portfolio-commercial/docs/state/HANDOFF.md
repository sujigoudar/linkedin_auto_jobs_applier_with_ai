# Current handoff — read this first if picking up cold

This is the living, current-snapshot counterpart to
`docs/agents/HANDOFF.md` (which describes the general convention) and
`ops/COMMERCIAL_RESUME.md` (the original 13-phase build's own handoff,
now historical). Update this file, not just the commit log, when a
session ends with real state the next session needs.

## Where this app actually stands right now

Real, tested, currently in `app/`: multi-tenant RLS-enforced Postgres
schema, the four-book append-only ledger, local auth (sign-in/sign-up/
verify/recovery), revocable JWT sessions with a real DB-checked
denylist, the restricted `relay_role` ingress boundary with a real
negative test proving it cannot write to command-authority tables, the
customer/admin/public web screens (CU-/AD-/PU- series, see
`docs/FEATURES.md`), AD-18 audit log + evidence manifest export, CU-06
real drawdown/win-rate reporting, dual-secret rotation for both
signature-verification paths, and a real single-command Docker Compose
install spanning both `signal-copier` and this app plus the in-process
relay.

Still genuinely open, honestly: publisher transport (Collective2/eToro/
CopyFactory build request shapes but never send), real Stripe wiring,
PAMM/MAM real-broker integration, the non-default portfolio-research
recipes, and six owner-only action cards that gate any live activity.
See `docs/PENDING_DECISIONS.md`, `docs/KNOWN_ISSUES.md`,
`docs/state/tasks.json`.

## Standing directive (still true)

From `ops/COMMERCIAL_RESUME.md`, dated 2026-09-27, still in force:
build everything real that can be built without external accounts/
infrastructure, keep everything paper-traded/non-live, and **stop and
notify explicitly before anything reaches a point of actually going
live** — a real Stripe charge, a real Collective2/eToro publish, a
real broker-managed account. This has not been superseded by any later
commit.

## How to pick this up

1. Read `docs/state/PROGRESS.md` for the real recent-work snapshot.
2. Read `docs/state/BLOCKERS.md` and `docs/state/tasks.json` for what's
   open and why.
3. Read `docs/process/DEVELOPMENT.md` and
   `docs/process/DEFINITION_OF_DONE.md` before writing any code.
4. Run the real test suite and confirm it's green *before* making any
   change, per `docs/agents/VERIFICATION.md` — don't trust a prior
   session's "all green" claim without re-confirming, the same lesson
   `64d596d` had to learn the hard way.
5. Work in your own worktree (`docs/process/GIT.md`), rebase before
   pushing, and update this file (plus `docs/state/PROGRESS.md`,
   `state.json`, `tasks.json`, `BLOCKERS.md`) before ending your own
   session if real state changed.

## What changed in the most recent real work (for quick orientation)

See `docs/state/PROGRESS.md`'s "most recent first" list — as of this
snapshot, the newest real work is revocable JWT sessions (`72efeae`),
before that the AD-18 evidence manifest export (`f752904`), before
that dual-secret rotation (`9ddc681`).
