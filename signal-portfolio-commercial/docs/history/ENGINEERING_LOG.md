# Engineering log

A narrative account of how `signal-portfolio-commercial` was actually
built, reconstructed from its own commit history and its own
`ops/` handoff documents. This is history, not a status report — see
`docs/state/PROGRESS.md` for the current snapshot.

## Phase 00–12: the original 13-phase build

The app began from a spec package
(`Signal_Portfolio_Commercial_Platform_Claude_Code_Pack_v1`,
`ops/commercial_state.json`'s own `package_version`) describing 114
requirements across a customer/admin/public commercial platform
wrapping the existing `signal-copier` trading engine. The build was
explicitly scoped by the user as "Do all" — a full 13-phase build, not
a narrower slice — and it proceeded phase by phase, each one closing
with real tests and a documented load-bearing verification pass:
temporarily break the new guarantee, confirm the specific test fails,
restore, reconfirm green. This discipline, visible from Phase 01
onward, is the same one every later slice and feature commit in this
app's history still follows (see `docs/agents/VERIFICATION.md`).

Early phases built the deterministic core with no external
dependencies: the rights registry (fail-closed `check_rights()`), the
tenancy/RLS/permission layer (real Postgres row-level security proven
against a genuine non-superuser login, a compound-FK cross-tenant
guard, a local JWT issuer), and the four-book append-only economic
journal (a real Postgres trigger rejecting direct mutation, corrections
as new rows referencing the original via `correction_of`).

From there the build hit real, honest boundaries. Portfolio research
(Phase 04) stopped at the deterministic half — enumeration and a single
equal-weight recipe — because the remaining recipes and evaluation
methods need real authorized historical data that didn't exist in this
environment, and the build's own rule was "never generate fabricated
returns." Publication (Phase 05) built the durable
`PublicationIntent` write-path and a fail-closed state machine, but no
destination adapter to actually send anything. Collective2 (Phase 06)
is the clearest example of the build's evidence discipline: asked to
resolve a real documentation conflict (Collective2's own docs disagree
on whether TIF value 2 is valid), the session attempted a `WebFetch` to
`https://collective2.com/apidoc/v4` and was refused by the
environment's own egress policy — confirmed as a real organization
denial, not a transient failure, via `/root/.ccr/README.md`. Rather
than guess, the `Tif` enum was built to define only the two undisputed
values, with the code raising rather than accepting the disputed one.
The same pattern repeats for eToro (Phase 07, demo-only structurally
enforced), Stripe (Phase 08, a real signature-scheme reimplementation
against synthetic payloads, no real account), and PAMM/MAM (Phase 10,
simulation-only, matching the spec's own "production uses the actual
contractual broker rule instead").

Phase 12 closed with `docs/12_validation_report.md` — an honest,
line-by-line audit of all 114 requirements: roughly 31 passed, 8
partial, 9 blocked on external infrastructure, 66 not yet run. Six
owner-only action cards (legal entity, source rights, platform
agreements, payment processor, customer agreements, the final go-live
decision) were identified as the standing gate on anything live, and
explicitly framed as decisions no amount of further coding could
advance. `ops/COMMERCIAL_RESUME.md` was written as the durable handoff
for whoever picked the build up next: per-phase status, what's wired
versus not, the standing "stop before going live" directive, and a
concrete next step (a real FastAPI app, real Alembic migrations —
neither existed yet at that point).

## The post-Phase-12 addendum and the real infrastructure push

The next real phase of work built exactly what `ops/COMMERCIAL_RESUME.md`
named as the next step: a real FastAPI application (`app/main.py`,
`app/api/`) wiring the existing services into real HTTP routes, and a
real Alembic migration chain replacing the test-only
`Base.metadata.create_all` setup. `ops/commercial_state.json`'s own
addendum note records the net effect plainly: several requirements
moved from partial to passed, the test count grew from 129 to 227.

## The Signal Platform Integration Correction Pack (slices 1–13)

A second major arc of work, evidenced by a long, tightly sequenced run
of commits (`c244dbd` through `0f14efe` and beyond), built the real
integration boundary between `signal-copier` (the trading engine) and
this commercial platform: `signal_platform_contracts` as a pure,
shared event-envelope package; a restricted relay worker and its own
`relay_role` Postgres login, deliberately scoped to the minimum grants
its tenant-discovery lookup needs; ordering and gap detection on the
resulting inbox; producer-generation binding to reject rollback or
reused sequence slots; per-analyst FIFO-lot P&L attribution; a real,
append-only control-plane audit trail on every command endpoint; and a
real end-to-end late-fee correction path. The arc closed with an
acceptance-case verification pass against the integration pack's own
40 supplemental cases (`1945c2e`), producing
`INTEGRATION_ACCEPTANCE_STATUS.md` — the same PASS/PARTIAL/BLOCKED/
NOT_RUN discipline as the Phase 12 validation report, applied to the
new integration surface.

This arc also produced INT-033, real-account single-writer
enforcement (`3b8cf40`) — extending the existing
`PublisherWriterClaim` durable-claim pattern one level up, because a
direct route and an external alias could otherwise both claim write
authority over the *same real brokerage account* underneath them, a
gap the existing per-route claim didn't cover.

## Making the installation actually real

INT-001 (`777dee1`) built a real, single-command Docker Compose
installation spanning both apps and the in-process relay, entirely in
`LOCAL_SIM`/paper mode with synthetic secrets. This did not land clean
on the first attempt — `2c1079a` ("Fix two more real deployment gaps
found by actually running containers") and `43e1ac0` ("Fix CI smoke
test: read owner token from a file, not masked logs") both exist
because the initial claim of "done" didn't survive contact with a real
CI runner and a real `docker compose up`. This is a genuine, concrete
example of why `docs/process/DEFINITION_OF_DONE.md`'s bar insists on
verification against real infrastructure rather than trusting a
plausible-looking diff.

## The screen build-out

A long sequence of commits through late September 2026 built out the
actual CU-/AD-/PU- customer, admin, and public screens named in the
spec — customer overview, portfolio selection, mandate wizard, alert
preferences, profile/security, admin candidate comparison, incidents,
managed programs, content publishing, and the public catalog,
comparison, methodology, and status pages. Each commit follows the
same pattern the earlier phases established: build the real,
deterministic part, disclose exactly what's still missing (no
activation pipeline for CU-10's pause action, no OAuth for CU-07/CU-08's
platform connections, no numeric rate field for AD-14's managed-program
setup), and prove it with tests.

## The most recent arc: security and evidence hardening

The most recent real work in this app's history, as of this log's own
writing, closes a set of genuine security and evidence gaps rather
than adding new screens:

- **CU-06's real drawdown/win-rate** (`64d596d`) — notable not just for
  what it built, but for what it found: a real regression left
  mid-verification by a prior session (a peak-tracking bug the
  existing load-bearing test hadn't caught, because that test's own
  true peak happened to sit immediately before its own true trough).
  The fix added a regression test specifically shaped to catch that
  bug class, then re-ran the full break/restore verification loop.
- **AD-18's evidence manifest export** (`f752904`) — a real,
  content-hashed, tamper-detectable export of the audit log, gated on
  a new permission and itself logged as an audit event.
- **CURRENT+PREVIOUS dual-secret verification** (`9ddc681`) — real
  zero-downtime secret rotation across both apps' signature-verification
  paths, fail-closed when both secrets are unset.
- **Revocable JWT sessions** (`72efeae`) — closing a real gap
  (previously cryptographically self-contained, unrevocable-before-
  expiry Bearer tokens) with a real, fail-closed, DB-checked denylist,
  and real owner/customer-facing revoke actions wired into the actual
  admin and customer screens rather than left as a backend-only
  capability.

## What this history demonstrates, in one sentence

Every phase of this build — from the earliest rights-registry work
through the most recent session-revocation hardening — follows the
same discipline: build the real, deterministic part that can actually
be proven in this environment, disclose exactly what a missing real
dependency blocks rather than fake it, and prove every guarantee by
breaking it on purpose and watching the right test fail before calling
anything done.
