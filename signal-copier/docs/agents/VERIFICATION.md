# The evidence bar for "done"

A claim of "done" in this repository is only as good as the evidence
behind it. This branch's own history sets a specific, higher-than-usual
bar — this document states it explicitly and shows where it came from.

## What "done" requires evidence of

**(a) The exact CI commands run, and their real output.** Not "ruff and
mypy pass" — which exact mypy invocation (see
`docs/process/DEFINITION_OF_DONE.md` item 2: the scoped file list from
`.github/workflows/signal-copier-ci.yml`, not `mypy .`), run against
which commit, with what actual output. Several commits on this branch
exist purely because this exact command, run for real, caught something
a less specific check would have missed (`eaa5c80`, `c59cd97`, `00665ce`,
`857ae01`, `ab08221`, `146935e`) — these are CI-only failures, several of
them order-dependent or environment-dependent, that would not have been
caught by "I read the diff and it looks fine."

**(b) A load-bearing test, confirmed to fail before the fix and pass
after.** A test that was only ever run against the already-fixed code is
not evidence the fix does anything — it might pass regardless of whether
the bug exists. The confirmed cycle is: revert the fix, run the test,
confirm it fails **for the right reason** (not an unrelated error),
restore the fix, confirm it now passes. Quoted directly from this
branch:

- `ae6a016`: *"Verified load-bearing: temporarily moved the ledger write
  in `_submit_order` to after the broker call and confirmed a simulated
  mid-call crash then loses the row entirely; reverted and reconfirmed."*
- `c6e4e7b`: *"Load-bearing verification: reverted each of the two named
  bugs' fixes in turn, confirmed the corresponding new test genuinely
  fails for the right reason, then restored and reconfirmed green."*
- `d363e79`: *"Load-bearing verification: reverted the ibkr.py fix
  alone, confirmed `test_get_order_status_filled_with_a_genuine_zero_fill_stays_zero`
  fails with `assert None == 0.0` (the exact old bug), then restored the
  fix and reconfirmed green."*

**(c) Confirmation the fix survived a rebase onto the latest shared
branch.** A fix verified before rebasing onto whatever else has since
landed on `claude/signal-copier-redesign` is evidence about a tree that
no longer exists once the rebase happens. `cdfee8b` exists specifically
because a rebase-time check (re-examining `alembic/versions/` against
the new tip) caught something a pre-rebase verification pass could not
have seen. Evidence for "done" needs a timestamp/commit reference *after*
the rebase, not just before it.

## Self-reports are not trusted at face value

None of the above is satisfied by an agent's own prose summary of what
it believes it did. The practice this branch's history repeatedly
follows is **independent re-verification from a fresh checkout**:

- The verification/integration role (`docs/agents/ROLES.md`) re-runs the
  full suite after a wave lands, specifically to catch interactions
  between individually-verified changes — `9d22b32`'s own commit message
  records finding and fixing *"an order-dependent flake ... found during
  full-suite verification"* that no individual agent's scoped test run
  would have surfaced.
- Several "Fix CI:" commits (`8deb326`, `923ebeb`, `ab08221`, `857ae01`,
  `00665ce`) are exactly this pattern: a change that looked complete and
  locally correct still failed once run for real in CI's actual
  environment (different installed extras, different import
  availability, different concurrency), and the fix only exists because
  someone re-ran it independently rather than accepting the original
  report.

**When reviewing or accepting someone else's "done":** ask for (a), (b),
and (c) specifically. If any is missing, re-run it yourself before
relying on the claim — this is not optional caution, it is the standard
this codebase's own history was built to.
