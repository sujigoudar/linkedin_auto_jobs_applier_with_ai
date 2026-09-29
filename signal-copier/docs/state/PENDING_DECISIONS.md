# Pending decisions

Real open questions surfaced by this branch's own code/commit history,
as of HEAD `cdfee8b`. These are decisions someone still needs to make or
work someone still needs to land — not settled matters restated for
completeness.

## `release_status` wiring to P0-7's qualification/release taxonomy

`app/main.py`'s `GET /system/readiness` reports `release_status` as an
honest, static placeholder:

> "No qualification/release-approval taxonomy exists yet in this build.
> FOLLOW-UP: integrate with the P0-7 qualification/release-state work
> once it lands."

**Status: genuinely still pending.** P0-7 has not landed on this branch.
`app/models.py` references it twice as the sibling effort that will own
this taxonomy (around the `release_status` field's own comment, and near
a "pending_review"-style status that's deliberately not yet an enum).
The open decision: what the taxonomy's actual states should be, and
whether `release_status` should be a coarse rollup or expose the
taxonomy's states directly — neither has been decided or built yet.

## `trading_authority` wiring to P0-6's writer lease

Same placeholder pattern, but P0-6 (the writer-lease fencing work it was
waiting on) **has since landed** (`55976eb`). `app/main.py` still
contains the pre-P0-6 placeholder text verbatim, including a FOLLOW-UP
comment referencing P0-6 as not-yet-landed. This is not a pending
*decision* so much as pending *work* — see `docs/state/tasks.json`'s
`trading_authority-wiring-to-P0-6` entry — but it's listed here too
because there is a real design question buried in it: should
`trading_authority.status` read `WriterLeaseGuard`'s in-memory fenced/
active state, the `writer_lease` table's row directly, or both (and if
both, which one wins when they briefly disagree during a renewal)? That
hasn't been decided.

## Owner-wide notional ceiling and risk-basis sizing: default-off, by design

`app.config.MAX_OWNER_NOTIONAL_EXPOSURE` and
`DestinationAccount.risk_percent_of_equity` (added in `c6e4e7b`, P0-3)
are opt-in, `None` by default. This is a deliberate decision already
made (default to no additional gate, not a numeric default), not an open
question — recorded here only so a future change doesn't "fix" it into a
numeric default without realizing that was intentional. See
`docs/ASSUMPTIONS.md`.

## Whether `command_ledger` becomes the read contract for restart recovery beyond P0-4

`ae6a016`'s commit message documents
`SignalStore.list_unresolved_command_ledger_entries(account_id=None)` as
*"the documented, stable read contract a sibling restart-recovery agent
(P0-4) depends on."* P0-4 (`d087bc0`, durable capital reservations) has
already landed and is presumably an early consumer, but whether any
*other* restart-recovery or reconciliation logic should also read
through this same contract (rather than the `orders` table directly) has
not been explicitly decided as a repo-wide convention.
