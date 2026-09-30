# Features

What is actually implemented, pulled from `app/api/`, `app/services/`,
and the requirement IDs (`CU-`/`AD-`/`PU-`/`ID-`/`INT-`) this app's own
commit history closes. "Implemented" here means real code with real
tests, not a design document — see `docs/KNOWN_ISSUES.md` for the
partial/stub boundary on each item below where one exists.

## Identity and access

- **Local sign-in/sign-up/email-verify/password-recovery** (ID-01/
  ID-02/ID-03, `app/services/local_auth.py`, `e303af9`) — argon2id
  password hashing, single-use expiring tokens, cookie-based sessions
  with per-mutation CSRF tokens. Self-service signup is scoped to the
  `CUSTOMER` role only.
- **Revocable JWT/Bearer-token sessions** (`app/services/auth.py`,
  `app/services/token_revocation.py`, `72efeae`) — a real, DB-checked
  denylist keyed by `jti`; fails closed on any error during the
  revocation check. Owner/customer-facing revoke-all-tokens actions
  wired into CU-13 and AD-16.
- **Permission model** (`app/services/permissions.py`) — an explicit
  role-permission allow-list, fail-closed by default for any unlisted
  action.
- **Row-level security / tenant isolation** (`app/db.py`) — Postgres
  `FORCE ROW LEVEL SECURITY` on every tenant-scoped table, plus bespoke
  public-visibility policies for `products` and `content_documents`
  (published rows visible to anonymous sessions; draft rows scoped to
  tenant).
- **Restricted relay ingress role** (`relay_role`, `app/db.py`,
  `app/api/relay_routes.py`) — a distinct, non-superuser Postgres login
  with exactly the grants the relay's own tenant-discovery lookup
  needs, real-tested to have zero write access to command-authority
  tables (`test_relay_role_has_no_write_access_to_any_command_authority_table`).

## Customer-facing (CU-)

- CU-01 customer overview / landing summary.
- CU-02 my portfolios (first real customer-selection foundation).
- CU-03 selected portfolio detail.
- CU-04/CU-05/CU-06/CU-11/CU-15 (`fc25d37`) — including CU-06's real
  max drawdown + FIFO-lot win rate.
- CU-07/CU-08 platform connections + connection wizard (DECLARED-only
  records — no real OAuth exists).
- CU-09 copy setup and mandate wizard — real mandate drafts.
- CU-10 pause copying / position handoff — real cancel-draft action
  (no activation pipeline exists to pause).
- CU-12 alert delivery preferences (the safety category cannot be
  excluded by design).
- CU-13 profile, security, and display preferences, including the
  revoke-all-tokens security action.

## Admin/owner/operator-facing (AD-)

- AD-01 incident linking (confirmed via session audit wiring,
  `76cf4d9`).
- AD-05 research run and full results (honest always-empty progress,
  no fabricated job execution).
- AD-06 candidate comparison/draft (`956bedb`).
- AD-10 publication intent and cohort detail.
- AD-11 mandates read view; real session audit.
- AD-13 pricing, entitlements, and billing operations (test-mode price
  drafts).
- AD-14 managed-program setup (real PAMM/MAM config, no numeric rate
  field).
- AD-15 managed allocations, NAV, and dealing (real program listing,
  honest NAV/dealing/cashflow gaps).
- AD-16 access control, including per-staff-member revoke-all-tokens.
- AD-17 integrations, data rights, and quotas (reviewed provider
  allowlist).
- AD-18 audit log and release evidence — a real append-only store, plus
  a real evidence manifest export with content-hash tamper detection
  (`f752904`).
- AD-19 content and disclosure publishing (plain-text drafts,
  submit-for-review).
- AD-20 workspace customization (shared tenant default, mandatory
  panels cannot hide).
- AD-21 commercial incidents (`956bedb`).
- AD-22 commercial deployments and recovery (real environment status,
  honest no-deployment-infra gap).

## Public site (PU-)

- PU-02 public product catalog (visible via the `products`
  visibility policy).
- PU-03 "Try our fit simulator" — real equity curve, now rendered as
  a real Chart.js line chart (`e95fc35`).
- PU-04 portfolio comparison (up to four, honest no-performance-series
  gap).
- PU-06 methodology, risk, and legal documents.
- PU-07 public service status (honest no-incident-model gap).

## Trading-integration correction pack (INT-/S<n> slices)

- `signal_platform_contracts` — the pure cross-service event envelope
  and identity contract both apps import.
- Restricted relay worker connecting `signal-copier` to the commercial
  inbox.
- Ordering and gap detection on the commercial inbox.
- Producer-generation binding (rejects rollback / reused sequence
  slots).
- Per-analyst P&L attribution (`Book.PLATFORM`, FIFO-lot method).
- Real, append-only control-plane audit trail on every command
  endpoint.
- Real end-to-end late-fee correction path through the inbox.
- Real-account single-writer enforcement (INT-033) — a durable claim
  one level above `PublisherWriterClaim`, so a direct route and an
  external alias for the same real brokerage account can't both claim
  write authority.
- CURRENT+PREVIOUS dual-secret signature verification, enabling
  zero-downtime secret rotation across both apps' signing boundaries.
- A rendered owner "Trading & Integration Status" page, and an
  acceptance-case verification pass against the integration pack's own
  40 cases.

## Billing / economics

- Four-book (source/model/platform/follower) append-only economic
  ledger (`app/models/ledger.py`, `app/services/ledger.py`) — Postgres
  triggers reject direct UPDATE/DELETE; corrections are new rows.
- Entitlement model separating `authorizes_new_entry` from
  `authorizes_risk_reducing_management` — a payment outage never
  revokes management of exposure that already exists.
- Stripe webhook signature verification + event-ID idempotency
  recording (real signing-scheme reimplementation, synthetic payloads
  only — see `docs/KNOWN_ISSUES.md`).

## Deployment

- Single-command local install: `docker compose up --build` brings up
  both real apps, Postgres, and the in-process relay in
  `LOCAL_SIM`/paper mode (`777dee1`, `docs/process/RELEASE.md`).
- A real Alembic migration chain (37 revisions) verified end-to-end in
  a dedicated CI step, separate from the test suite's own schema setup.

For what's deliberately NOT built or only partially built behind each
of these, see `docs/KNOWN_ISSUES.md` and `docs/TECH_DEBT.md`.
