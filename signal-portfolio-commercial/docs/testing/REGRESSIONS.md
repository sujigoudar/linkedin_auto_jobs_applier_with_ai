# Regression Catalog

A real catalog of gaps/bugs this codebase has actually found and closed,
pulled from commit history. Kept so the same class of bug doesn't get
silently reintroduced, and so the load-bearing regression test that
guards each one is easy to find. See `docs/standards/TESTING.md` §2 for
the "load-bearing verification" convention these entries all follow.

## INT-027 -- routing-outcome taxonomy gap (source coverage)

**Gap:** `app/services/source_coverage.py`'s coverage report could tell
you a `SOURCE_RECEIPT`'s disposition (`LEDGER_RECORDED` / `PARKED` /
`RECEIVED_NO_LEDGER_ENTRY`), but not *why* it was routed the way it was --
a receipt was exported unconditionally, before routing, for every
non-CLOSE signal, but the receipt itself never encoded which routing
outcome (admitted / rejected / unfilled / error / not-routed /
disabled-by-settings) actually happened downstream.

**Fix (`edbe3a8`, "INT-027: encode the real routing/admission/fill
outcome per SOURCE_RECEIPT"):** a new, real, separate
`ROUTING_ADMISSION_OUTCOME` event (`signal-copier`'s own
`app/engine.py::_export_routing_outcome`), correlated back to its
originating `SOURCE_RECEIPT` **by `event_id`** (not by `source_stream`
alone, which would be ambiguous), applied onto `InboxEvent.
routing_outcome` (migration `f4b2c8e0a913_inbox_events_routing_outcome`),
and surfaced through `source_coverage.py`'s report and the
`GET /api/v1/ops/source-coverage` route.

**Honest scope preserved, not silently dropped:** the fix commit is
explicit that no `"canceled"` code path exists anywhere in the engine,
and `"loss"`/`"commentary"` are not routing outcomes at all (a loss is a
P&L fact computed later, never a routing/admission state) -- neither is
fabricated to fill out the taxonomy. See `source_coverage.py`'s own
module docstring (`docs/standards/CODING.md` §1) for the full statement.

**Load-bearing regression test:** the commit message states the exact
verification: "breaking the `event_id` correlation (matching by
`source_stream` instead) fails exactly the cross-receipt correlation
test; restored and reconfirmed green." Guard this in
`tests/test_source_coverage.py`; if a future refactor changes how
`routing_outcome` is correlated back to its receipt, re-run this same
break/restore check.

## INT-040 -- storage-ceiling gap (sibling repo, `signal-copier`)

**Gap:** `signal-copier`'s private export outbox (`export_events`) had no
measured size, no configurable ceiling, and no alerting signal -- an
unbounded-growth table with nothing watching it.

**Fix (`signal-copier`'s own `bb61055`, "INT-040: real storage-ceiling/
alerting policy for the export outbox"):** a real, live measurement
(`SignalStore.export_outbox_backlog`: `SUM(LENGTH(envelope_json))` over
undelivered rows -- never an estimate or the whole DB file's size), a
configurable `EXPORT_OUTBOX_SIZE_CEILING_BYTES` (256 MiB default), wired
into `GET /health` and the TR-16 Subsystems table as an
**informational-only** signal (same tier as `relay_ok`/
`provider_scout_ok` -- deliberately never auto-blocking), and a
standing test verifying no code path anywhere prunes, truncates, or
discards `export_events` rows.

**Why it's in this catalog:** this commit lives in the sibling
`signal-copier` repository, not `signal-portfolio-commercial` -- no file
under `signal-portfolio-commercial/` changed. It's recorded here because
(a) the pattern (real measurement, documented ceiling, informational
health signal, never silent pruning) is exactly the one to reach for if
one of *this* service's own append-only tables (`ledger_entries`,
`audit_events`, `portfolio_versions`) ever needs the same treatment (see
`docs/standards/PERFORMANCE.md` §3), and (b) it's the same
`export_events`/`inbox_events` pipeline this service's own
`app/services/integration_inbox.py` is the other, consuming half of --
a real growth problem on the producing side is directly relevant to
anyone maintaining the consuming side.

## CU-06 -- max-drawdown peak-tracking bug

**Bug:** the running-peak tracker in the max-drawdown computation
(ported from `signal-copier/app/statistics.py::compute_max_drawdown`'s
running-peak walk) was **overwriting the running peak with every point**
instead of only on a new high -- so the "peak" used to compute drawdown
at any given point was really just "the previous point's own value," not
the true highest point seen so far.

**Why the original test didn't catch it:** the fix commit (`64d596d`)
states plainly: "the original load-bearing test alone did not [catch
this], because its own true peak sits immediately before its own true
trough" -- i.e. for that specific fixture's data shape, "previous point"
and "true running peak" happened to coincide, so the bug was invisible to
that one test even though it was a real, active defect.

**Fix + new load-bearing test:** `tests/
test_customer_performance_report.py::
test_max_drawdown_uses_the_true_running_peak_not_just_the_previous_point`
was added with a fixture shaped specifically so the true peak and the
previous point *diverge* (a peak, then a partial pullback and partial
recovery, then the real trough) -- a shape the old test's fixture didn't
have. Verified exactly per `docs/standards/TESTING.md` §2: "broke the fix
back to 'peak = previous point', confirmed only the new test failed with
the exact predicted wrong numbers (100/2min instead of 150/4min),
restored, reconfirmed all 7 tests green."

**Lesson for future fixture design:** a regression test's fixture must be
shaped so the bug it guards against would actually change the *answer* --
not just exercise the code path. When two computations (`true peak` vs.
`previous point`) could coincide for a given input, deliberately choose
fixture data where they diverge.

## Revocable JWT sessions -- unrevocable-before-expiry gap

**Gap:** Bearer-token JWTs (`app/services/auth.py::issue_token`/
`verify_token`) were cryptographically self-contained and therefore
**unrevocable before their natural expiry** -- there was no way to kill a
live session (e.g. after an owner revokes a staff member, or a customer
reports a compromised token) short of waiting out the token's own TTL.

**Fix (`72efeae`, "Add revocable JWT sessions: real jti-keyed denylist,
fail-closed verification, UI"):**

- `issued_tokens`/`revoked_tokens` tables
  (`app/models/token_revocation.py`, migration `c1d2e3f4a5b6`) back a
  real, DB-checked denylist keyed by each token's own `jti`.
- `issue_token` mints a fresh `jti` per token and records it via
  `record_issued_token` whenever a DB session is available.
- `verify_token` (the one real request-verification call site, via
  `app/api/dependencies.py::get_current_scope`) checks the denylist
  *after* decoding, and **fails closed**: "any exception raised while
  checking revocation is treated as rejection, never as 'not revoked'."
  See `docs/standards/ERROR_HANDLING.md` §3.
- Two real revoke actions (`CU-13`'s own account settings, `AD-16`'s
  per-staff-member action) both call `revoke_all_tokens_for_user`, which
  denylists every currently-active `jti` for that user/tenant and appends
  a real `AuditEvent` (`action=revoke_all_tokens`) via
  `append_audit_event` -- see `docs/standards/LOGGING.md`.

**Load-bearing regression tests
(`tests/test_token_revocation.py`):** real end-to-end HTTP coverage --
issue a token, use it successfully once against a real route, revoke it
through the real route, confirm the *exact same* token is genuinely
rejected (401) on the very next request -- plus two explicit fail-closed
proofs: a monkeypatched denylist-check failure (must reject, not allow),
and a real `revoke_token` regression guard. The commit states these were
"manually confirmed ... fail when `verify_token` is changed to skip the
denylist check, and pass again once restored" -- the same break/restore
discipline as every other entry in this catalog.

## How to use this catalog

When you touch code near one of the areas above (`source_coverage.py`,
the max-drawdown/equity-series computation, `auth.py`'s token
verification, or the append-only export/inbox pipeline), re-read the
matching entry first. If your change could plausibly reintroduce the bug
class described, re-run (or re-derive) the same break/restore check the
original fix used, not just the existing test suite as-is.
