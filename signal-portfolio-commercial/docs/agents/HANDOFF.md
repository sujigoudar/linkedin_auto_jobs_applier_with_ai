# Handing off to the next session

This app already has a real, working handoff artifact —
`ops/COMMERCIAL_RESUME.md` and `ops/commercial_state.json` — written
specifically so a session picking this up cold does not have to
re-derive the state of the world from the commit log. This document
describes that same discipline as a general convention for every
session, not just the original 13-phase build.

## What a good handoff actually contains, per this app's own precedent

`ops/COMMERCIAL_RESUME.md` models the pattern:

1. **A per-unit status, not a vague summary.** Each phase/slice is
   marked `done`, `partially_done`, or not yet started, with the exact
   files that implement it and the exact tests that prove it — never
   "mostly working" without a pointer to what "mostly" excludes.
2. **An explicit "not yet wired" note per unit.** Nearly every phase
   entry ends with exactly what still doesn't call the new code yet,
   e.g. "`check_rights()` has no callers yet" — so the next session
   doesn't assume integration that hasn't happened.
3. **A "read this first if picking up cold" section**, stating plainly
   what's real and tested versus what's still open, and naming the
   authoritative documents to read before doing anything that could be
   mistaken for "finishing."
4. **The standing directive/constraints repeated**, not just linked —
   fail-closed discipline, Postgres-only testing, and (critically) the
   live-money stop condition: never cross into a real charge, a real
   publish, or a real broker-managed account without stopping and
   surfacing that explicitly first.
5. **A concrete "how to run the tests" section** with the exact
   commands and the one real gotcha already hit (`initdb` permission
   errors on pytest's tmp dir ancestors).
6. **A real "next step"** — not a wishlist, but the actual smallest
   next unit of work and why it's next.

## A real example of what this catches

`64d596d`'s own commit message documents finding a bug "left
mid-verification by the prior session" — a real regression in the
drawdown peak-tracking logic that the prior session's own load-bearing
test had not caught (because that test's own true peak happened to sit
immediately before its own true trough, which meant the bug it was
supposed to guard against didn't actually change that test's result).
The next session found this specifically because it re-ran the full
verification loop rather than trusting the prior session's "green"
status at face value, and it left a *stronger* regression test behind
for the session after that.

## What to update before ending a session

- **`docs/state/PROGRESS.md`** — if the session closed a real gap
  worth noting in the running snapshot (a new CU-/AD-/PU- item, a
  security-relevant change like a new revocation mechanism, a schema
  change).
- **`docs/state/state.json` / `docs/state/tasks.json`** — machine-
  readable mirrors of the same; update alongside, not instead of, the
  human-readable docs.
- **`docs/state/BLOCKERS.md`** — if the session hit a real external
  block (an egress-policy denial, a missing credential, a spec
  ambiguity that needs a human decision) — document it the way
  `ops/COMMERCIAL_RESUME.md`'s Phase 06 entry documents the
  Collective2 TIF=2 WebFetch denial: what was tried, what blocked it,
  and what would unblock it.
- **`docs/state/PENDING_DECISIONS.md`** — if the session surfaced a
  new owner-only decision, add it next to the six standing action
  cards rather than leaving it only in a commit message.
- **`docs/history/ENGINEERING_LOG.md`** — for anything significant
  enough to belong in the narrative account, not just the snapshot.

## What NOT to do

- Do not mark a phase/requirement "done" in any state doc without a
  currently-passing, cited test — this is the same rule
  `INTEGRATION_ACCEPTANCE_STATUS.md` applies at the acceptance-case
  level.
- Do not silently drop a previously-documented gap because it's
  inconvenient to still be open — if it's still open, say so again in
  the updated snapshot.
- Do not leave a broken intermediate state (a half-applied refactor, a
  failing test committed "to fix later") without saying so explicitly
  in both the commit message and `docs/state/BLOCKERS.md` — a silent
  broken state is exactly what `64d596d` had to discover the hard way.
