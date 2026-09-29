# Current progress snapshot

As of `HEAD` = `cdfee8b1dc4d5daea58e178ce679aff37adecba8` on
`claude/signal-copier-redesign` (2026-09-29). This is a snapshot, not a
roadmap — update it when the state it describes actually changes.

## What wave this is

The branch is in the middle of the **P0 audit-response foundation wave**:
a sequence of fixes responding to an external release-readiness audit,
landing in roughly this order (oldest first):

1. `d363e79` — P0-9: fail-closed sweep (genuine-zero-collapses-to-None
   bugs in `app/brokers/ibkr.py`, `app/sources/rithmic.py`)
2. `c6e4e7b` — P0-3: capital allocator fail-closed sizing, unresolved-
   exposure block, owner-wide + risk-basis gates
3. `c88bb66` — AUD-01: distinct-field quantity model replacing optimistic
   PENDING-order accounting
4. `a5d5aec` — per-exact-route live qualification ladder
5. `9d9e5a6`, `230d399`, `fe6dd76` — CI fix, `exclusive_writer_qualified`
   docs, plain-account CLOSE reconciliation
6. `9d22b32` — P0-8: readiness split into 6 independent dimensions, with
   honest `trading_authority`/`release_status` placeholders
7. `00665ce` — CI fix (guard ccxt-dependent tests)
8. `ae6a016` — P0-2: `command_ledger`, a durable pre-effect ledger for
   every broker command
9. `55976eb` — P0-6: cross-process fencing + manual-only writer-lease
   failover
10. `cdfee8b` (current HEAD) — rebase-time fix: renumbered the
    writer_lease migration from `0014` to `0015` after `command_ledger`
    claimed `0014` first (see `docs/process/GIT.md`)

Before this wave, the branch went through a long sequence of per-screen
redesign work (the TR-0X/CU-0X/AD-0X trading/customer/admin screens, a
shared design-system foundation, broker-adapter additions, real
Chart.js analytics) — that work is stable and not what's currently
in flight. See `docs/history/ENGINEERING_LOG.md` for the fuller
narrative and `CHANGELOG.md` for the itemized list.

## What's genuinely landed and working, as of HEAD

- Fail-closed fixes across the broker/source fill-data path and the
  capital allocator (items 1–3 above).
- A durable, pre-effect command ledger for every broker command
  (`app/command_ledger.py`), verified load-bearing against a simulated
  mid-call crash.
- A real cross-process writer-lease fencing mechanism
  (`app/writer_lease.py`, `app/promote_cli.py`) — a second site can no
  longer become an active writer while an existing lease is valid, and a
  fenced-out process is refused on its very next command check, not just
  once its lease looks expired. See `docs/FAILOVER.md`.
- A 6-dimension `/system/readiness` endpoint (`app/main.py`) that can no
  longer let one dimension (e.g. `liveness=up`) mask another being
  unknown or degraded.
- Alembic head is `0015` (`alembic/versions/0015_add_writer_lease_table.py`).

## What's genuinely NOT yet landed / still open

- **`trading_authority` in `/system/readiness` is not wired to the P0-6
  writer lease that landed in `55976eb`.** `app/main.py` still contains
  the placeholder branch and its `FOLLOW-UP: integrate the P0-6
  fencing/lease work once it lands` comment, even though P0-6 has since
  landed on this same branch. This is a real, currently-stale gap — see
  `docs/state/tasks.json` and `docs/state/PENDING_DECISIONS.md`.
- **`release_status` is still an honest `not_tracked` placeholder** —
  P0-7 (a qualification/release-approval taxonomy) has not landed yet.
  See `docs/state/PENDING_DECISIONS.md`.
- No formal release process exists (`docs/process/RELEASE.md`) — this
  remains pre-1.0, development-branch software.

## Verification status

Last known-verified state, per this task's own read of the repository
(not re-run in this documentation-only pass — see
`docs/agents/VERIFICATION.md` on why that distinction matters): HEAD's
own commit message (`cdfee8b`) is a mechanical migration rename with no
accompanying re-run described in the commit body; the prior substantive
commit (`55976eb`) documents its own load-bearing verification in full
(quoted in `docs/process/DEFINITION_OF_DONE.md`). A fresh
ruff/mypy/pytest run against current HEAD has not been captured by this
documentation pass and should be treated as **not yet independently
re-verified** — see `docs/state/BLOCKERS.md`.
