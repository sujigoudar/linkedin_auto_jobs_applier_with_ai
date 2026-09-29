# Engineering log

A narrative account of this branch's real engineering history, longer
and more discursive than `CHANGELOG.md`. Written from the commit history
itself — quotes are real commit-message text, not paraphrase presented as
quote.

## The shape of the work

`claude/signal-copier-redesign` is a single shared branch that many
agent sessions have pushed to over an extended period (275 commits ahead
of `main` as of this writing). The work falls into two broad,
chronologically distinct phases: a long **redesign phase** rebuilding the
operational dashboard and closing integration gaps screen-by-screen and
feature-by-feature, followed by a **foundation-hardening phase**
responding to an external release-readiness audit. The most recent ~15
commits, at the time this log was written, are entirely the second
phase.

## The redesign phase

Early work in this phase establishes shared infrastructure before
building on it — the pattern `docs/agents/HANDBOOK.md` calls a
"foundation wave": `3d8e43b` ("Add design-system foundation: 3-level
tokens, shared components, grouped nav") lands before the wave of
per-screen work that then consumes those tokens and components. From
there, screens are rebuilt in numbered batches — `TR-01` through `TR-16`
across four batches (`5bb51f1`, `67fd5bf`, `d68ace9`, `43decc7`), each
batch's commit message explicitly framing it as "batch N/4" — a visible
signal that this was planned and sequenced work, not ad hoc rewrites.

Real data-model work threads through this phase too, not just UI: real
order `purpose`/`family_id` and `PaperBroker` cash/fee tracking
(`25582fc`), an append-only stop/target lifecycle event log
(`1e288d7`), rolling statistics and pairwise correlation
(`8943e07`), and real execution-latency and MAE/MFE tracking feeding the
screens built on top of them. Several commits are explicit about finding
and fixing something previously wrong rather than just adding new
surface — `e48ec15` fixes `orders.submitted_at`/`protection_confirmed_at`
missing from `_COLUMN_MIGRATIONS`, and `f8f4650` fixes legacy dashboard
content bleeding through under the new `TR-0X` routes.

Broker/source coverage grows substantially in this phase: Tradovate,
OANDA, TradeStation, Tastytrade, Schwab, and Robinhood adapters are all
added as separate, individually-committed units of work, each with a
docstring establishing exactly how it was verified (reading a specific
well-established community wrapper's source directly, rather than
guessing at API shapes) and what risk it carries (Schwab and Robinhood
both explicitly, prominently documented as having no sandbox at all).
NinjaTrader as a signal source is solved the same way — `ninjascript/
SignalCopierAutoJournal.cs`'s introducing commit documents reading two
real, public reference implementations directly before writing a fresh
implementation modeled on the verified pattern, rather than either
copying unlicensed code or guessing.

A steady stream of "Fix CI:" commits runs throughout this phase
(`8deb326`, `923ebeb`, `2dfaa8e`, `ab08221`, `f999e50`, `78f87ab`,
`a06baac`, `2de2d7e`, `c59cd97`, `146935e`, `43e1ac0`) — each one a real,
specific CI-environment failure (a missing `importorskip` guard around
an optional SDK, a dependency not declared, a test relying on an
implicit cwd-based `sys.path`) caught only once the change actually ran
in CI's real environment, not by local reasoning about the diff. This is
the concrete evidence behind `docs/agents/VERIFICATION.md`'s claim that
self-reports aren't trusted at face value — several of these fixes exist
specifically because an earlier "done" turned out to be true only
locally.

## The foundation-hardening phase: the P0 audit-response wave

Later in this branch's history, an external release-readiness audit is
referenced explicitly across a cluster of commits numbered `P0-2` through
`P0-9` (not landing in strict numeric order). This wave is narrower in
scope than the redesign phase but deeper in rigor — nearly every commit
in it documents an explicit load-bearing verification in its own message
(see `docs/process/DEFINITION_OF_DONE.md` for the full quotes), a
practice that's present but less consistently narrated in the earlier
redesign-phase commits.

The wave's real findings, in the order they were fixed:

- **A fail-closed sweep (P0-9, `d363e79`)** found the same bug shape
  twice, independently, in two different adapters: `app/brokers/ibkr.py`
  and `app/sources/rithmic.py` both used an `x or None`-style pattern on
  fill price/quantity, silently turning a genuine `0.0` into `None`.
  Downstream, a `None` fill falls back to "unknown, assume the full
  requested quantity" — so a real zero-share fill risked being reported
  as a full one. For Rithmic specifically, the same collapsed value fed
  `app/engine.py`'s notional-exposure ceiling check, which skips
  entirely when price is `None` — meaning this wasn't just a display
  bug, it was a silent admission-check bypass.
- **The capital allocator (P0-3, `c6e4e7b`)** had two related fail-open
  gaps: a signal with no price used to silently skip the whole admission
  check whenever a gate was configured (a skip is economically an
  unbounded admit), and a position with an unresolvable
  `average_cost` contributed exactly `0.0` to computed exposure instead
  of being flagged as genuinely unknown. Both were closed to fail
  closed, and the module gained an owner-wide notional ceiling and
  opt-in risk-basis sizing as part of the same pass.
- **Optimistic PENDING-order accounting (AUD-01, `c88bb66`)** — the plain
  execution path applied the FULL requested quantity to tracked
  positions before any broker confirmation, whenever a broker only
  reported PENDING. A rejected or partially-filled order left the
  system's own record of live holdings silently wrong until a later
  reconciliation pass caught up. Replaced with five distinct, named
  quantity fields, with `positions.net_quantity` now updated only from a
  broker-confirmed fill.
- **A durable, pre-effect command ledger (P0-2, `ae6a016`)** closed a gap
  where a process killed between deciding to submit a command and the
  `orders` table write landing left no trace the command was ever
  attempted. Every real broker-command call site now writes a ledger row
  before calling the broker, confirmed load-bearing by literally moving
  the write to after the broker call and confirming a simulated mid-call
  crash then loses the row.
- **Cross-process writer-lease fencing (P0-6, `55976eb`)** added the
  actual mechanism `docs/FAILOVER.md` documents: a fencing-token
  protocol that refuses a second site's attempt to acquire writer status
  while an existing lease is valid, and fences out a stale in-memory
  holder on its very next command check rather than waiting for the
  lease to look expired.
- **Readiness split into 6 independent dimensions (P0-8, `9d22b32`)**
  closed the "reachable does not mean ready" gap the audit named — the
  previous `GET /health`/`GET /system/info` folded broker reachability,
  unknown balances, stale price data, and missing protection confirmation
  into one green/red signal. The new dimensions are honest about what
  isn't built yet: `trading_authority` and `release_status` are explicit
  `not_held`/`not_tracked` placeholders naming the P0-6 and P0-7 work
  they're waiting on, rather than fabricating a "held" or "approved"
  state. A genuine order-dependent test flake was also found and fixed
  during this commit's own full-suite verification pass.
- **The rebase-time coda (`cdfee8b`)**: `command_ledger` (P0-2) and
  `writer_lease` (P0-6) each independently added their own Alembic
  migration numbered `0014`, chaining onto the same `0013` base each
  worktree had last seen. Once `command_ledger`'s `0014` landed on the
  shared branch first, the very next commit renumbers `writer_lease`'s
  migration to `0015` and updates its own in-file chain comment — a
  real, concrete instance of the collision-and-renumber pattern
  `docs/process/GIT.md` documents as the normal, expected way this
  branch handles concurrent migrations.

## What this leaves open, as of the current head

`app/main.py`'s `trading_authority` placeholder still contains its
original FOLLOW-UP comment referencing P0-6 as not-yet-landed, even
though P0-6 landed in this very wave — a real, stale gap, not resolved
by anything in this log's account. `release_status` remains an honest,
still-accurate placeholder, since P0-7 has not yet landed on this branch.
See `docs/state/PROGRESS.md` and `docs/state/tasks.json` for the current,
maintained snapshot of both.
