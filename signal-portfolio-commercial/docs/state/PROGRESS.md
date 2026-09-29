# Progress snapshot

Last updated from real commit history through `72efeae` (HEAD of
`claude/signal-copier-redesign` at the time this snapshot was written).
This is a point-in-time account — see `docs/history/ENGINEERING_LOG.md`
for the full narrative and `ops/commercial_state.json` /
`ops/COMMERCIAL_RESUME.md` for the original 13-phase build's own
detailed per-phase notes (now historical; much has been built since).

## What's real and tested, most recent first

- **Revocable JWT sessions** (`72efeae`) — Bearer-token JWTs were
  previously cryptographically self-contained and unrevocable before
  natural expiry. Now: `issued_tokens`/`revoked_tokens` tables
  (`app/models/token_revocation.py`, Alembic `c1d2e3f4a5b6`) back a
  real, DB-checked denylist. `issue_token` mints a fresh `jti` per
  token; `verify_token` (the one real request-verification call site,
  `app/api/dependencies.py`'s `get_current_scope`) checks the denylist
  after decode and **fails closed** — any exception during the
  revocation check is treated as rejection, never as "not revoked."
  Two real revoke actions: a customer's own "revoke all API tokens"
  (CU-13) and a staff member's per-user revoke (AD-16), both logged as
  a real `AuditEvent`. 5 new tests, end-to-end HTTP coverage including
  two fail-closed proofs.
- **AD-18 evidence manifest export** (`f752904`) — a real, synchronous
  JSON export of filtered `AuditEvent` rows plus a manifest header
  (generated-at/by, filter criteria, row count, a content hash via
  `signal_platform_contracts.compute_payload_hash`). Gated by a new
  `export_evidence_manifest` permission (same OWNER/REVIEWER pair as
  `view_audit_log`); the export itself is logged as a new `AuditEvent`.
  Load-bearing tamper-detection test: the content hash changes when a
  bundled row is altered.
- **Secret rotation — CURRENT+PREVIOUS dual-secret verification**
  (`9ddc681`) — both `catalog-fit-sim` (signal-copier) and relay
  (signal-portfolio-commercial) signature verification now accept
  either the CURRENT or an optional PREVIOUS configured secret
  (`hmac.compare_digest`, same constant-time comparison as before),
  enabling zero-downtime secret rotation. Fails closed when both are
  unset.
- **CU-06 real max drawdown + win rate** (`64d596d`) — a real
  chronological cumulative-realized-P&L equity series from a
  customer's own `Book.FOLLOWER` ledger entries (never a fabricated
  indexed-to-100 return series — this build tracks no real
  starting-capital baseline), max drawdown ported directly from
  `signal-copier/app/statistics.py::compute_max_drawdown` (not
  reimplemented), and a completed-episode win rate reusing
  `analyst_attribution.py`'s own FIFO-lot open/close tracking
  (INT-026). Fixed a real peak-tracking regression left mid-
  verification by a prior session; added a regression test
  specifically shaped to catch that bug class.
- **INT-027 routing/admission/fill outcome encoding** (`e95fc35`
  precedes; `e95fc35`/`e635...` line) and **PU-03 fit-simulator equity
  curve as a real Chart.js line chart** (`e95fc35`) — the public "Try
  our fit simulator" panel's real `equity_curve` (from signal-copier's
  fit-simulation response) is now actually plotted, using the same
  vendored Chart.js `signal-copier` already ships, mounted the same
  way. Honest empty state when there are zero fitting trades.
- **Session audit wiring** (`76cf4d9`) — real session audit wired into
  AD-16/AD-11, AD-01 incident linking confirmed.
- **Customer-portal screens** (`fc25d37`) — CU-04/CU-05/CU-06/CU-11/
  CU-15 built.
- **AD-06/AD-21** (`956bedb`) — candidate comparison/draft and
  commercial incidents.
- **Local auth system** (`e303af9`) — real sign-in/sign-up/verify/
  recovery (ID-01/ID-02/ID-03): argon2id password hashing (`pwdlib`),
  single-use expiring email-verification/reset tokens, cookie-based web
  sessions with a separate CSRF token per mutation. Email delivery is
  not wired (no SMTP/SendGrid account exists) — the link is shown
  directly on the page instead, the same disclosed-gap pattern used
  elsewhere. Self-service signup is scoped to `CUSTOMER`, never
  `OWNER`.
- **Integration relay/inbox pipeline** (slices 1–13, `c244dbd` through
  `0f14efe`) — `signal_platform_contracts`, the restricted relay
  worker and `relay_role` boundary, ordering/gap detection, producer-
  generation binding, per-analyst FIFO-lot P&L attribution, real
  end-to-end late-fee correction, a rendered Trading & Integration
  Status page, an acceptance-case verification pass against the
  integration pack's own 40 cases.
- **INT-001 real single-command installation** (`777dee1`, plus fix
  commits `2c1079a`, `43e1ac0`) — `docker compose up --build` brings up
  both real apps, Postgres, and the in-process relay, entirely in
  `LOCAL_SIM`/paper mode. The two fix commits exist because the
  initial claim of "done" didn't survive actually running the
  containers — a real, demonstrated example of why
  `docs/process/DEFINITION_OF_DONE.md`'s bar matters.

## What predates this window (the original 13-phase build)

Rights registry, tenancy/RLS/permissions, the four-book append-only
ledger, portfolio research (deterministic half only), publication
intent write-path, Collective2/eToro adapter request-shape mapping
(never transmits), subscriptions/entitlement/Stripe-webhook
verification (never a real Stripe call), onboarding state machine,
PAMM/MAM simulation-only accounting, the model-gateway permission
boundary (no real LLM provider called). See
`ops/COMMERCIAL_RESUME.md` and `ops/commercial_state.json` for the
full, honest per-phase account, and `docs/12_validation_report.md` /
`docs/12_addendum_post_phase12_work.md` for the requirement-by-
requirement audit those phases were checked against.

## Current scale (approximate, from this snapshot)

- 37 Alembic revisions (`alembic/versions/`).
- ~15,600 lines across `app/`.
- ~800+ test functions across `tests/`, all run against a real
  disposable Postgres cluster.
- CI: lint (ruff) + type check (mypy) + `pytest -q` + a dedicated
  `alembic upgrade head` verification step against a second, fresh
  disposable cluster.

## What is still genuinely open

See `docs/KNOWN_ISSUES.md`, `docs/TECH_DEBT.md`, and
`docs/PENDING_DECISIONS.md` — PAMM/MAM remains simulation-only,
Collective2/eToro/CopyFactory adapters build request shapes but never
transmit (no real credentials exist), Stripe webhook verification is
real but no real Stripe account or processor call exists, and six
owner-only action cards (legal entity, source rights, platform
agreements, payment processor, customer agreements, go-live decision)
remain outstanding and cannot be advanced by more coding.
