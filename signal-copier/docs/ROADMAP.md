# Roadmap

Real, currently-pending direction, not a marketing wishlist — pulled from
what this branch's own commit history and code actually name as coming
next. See `docs/state/PROGRESS.md` for the current wave and
`docs/state/tasks.json`/`docs/state/PENDING_DECISIONS.md` for the
itemized open work behind each item here.

## Immediate: finish the P0 audit-response foundation wave

The branch is mid-way through responding to an external release-
readiness audit (see `docs/history/ENGINEERING_LOG.md` for the full
narrative). What remains open in that wave, as of HEAD `cdfee8b`:

- **Wire `trading_authority` to the now-landed P0-6 writer lease.** The
  fencing mechanism (`55976eb`) exists; `/system/readiness` doesn't read
  it yet. This is the most immediately actionable item on the roadmap —
  no other work blocks it. See `docs/state/tasks.json`.
- **P0-7: a real qualification/release-approval taxonomy**, which
  `release_status` in `/system/readiness` is currently an honest
  placeholder waiting on. Not started.

## Next: close the audit's remaining named gaps

The P0-* numbering in this branch's commits (P0-2 through P0-9, not all
sequential in landing order) suggests a tracked list of audit findings
being worked through one at a time; P0-7 is the clearest visible gap in
that sequence still open on this branch.

## Longer-term / disclosed-but-not-scheduled

These are real, explicitly disclosed gaps with no evidence of active
work on this branch — not rejected, just not yet in flight:

- **NinjaTrader signal-source, NinjaScript-side verification.** The code
  exists (`app/sources/ninjatrader.py`,
  `ninjascript/SignalCopierAutoJournal.cs`) but has never been run
  against a real NinjaTrader install. Needs a Windows + NinjaTrader
  environment this sandbox does not have — see `docs/state/BLOCKERS.md`.
- **Capital allocator scope expansion**: multi-currency/FX conversion,
  per-analyst overlap accounting, portfolio stress-loss modeling,
  cross-account netting beyond plain summation — all explicitly named as
  out of scope in `app/capital_allocator.py`'s own docstring, with no
  indication of being scheduled.
- **A formal release process.** Currently none exists
  (`docs/process/RELEASE.md`). Versioned releases, a changelog cadence,
  or a promotion pipeline from this shared dev branch to something more
  stable would be new process, not an extension of anything that exists
  today.

## What this roadmap deliberately does not claim

No dates, no version numbers, no committed scope beyond what's named
above — this is pre-1.0, single shared-branch, development software (see
`docs/process/RELEASE.md`). This document should be corrected the moment
any item above lands or is explicitly deprioritized, not left to describe
a stale wave once a new one starts.
