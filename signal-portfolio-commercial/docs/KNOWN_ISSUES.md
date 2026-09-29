# Known issues

Genuine, disclosed gaps — pulled from this codebase's own docstrings,
`ops/commercial_state.json`, and `INTEGRATION_ACCEPTANCE_STATUS.md`
(repo root), not a generic disclaimer list. Each item says exactly
what's real versus missing.

## Publisher/broker integrations are request-shape-only — nothing transmits

- **Collective2 (`app/services/collective2_publisher.py`)** builds a
  correct API4 Order envelope from a `PublicationIntent` but never
  sends it — no C2 sandbox or credentials exist in this environment.
  The `Tif` enum deliberately omits the disputed value `2` (see
  `docs/PENDING_DECISIONS.md`) and `build_order` raises rather than
  guessing. No StrategyId/symbol resolution, no OCA/stop-target group
  reconciliation, no external-acknowledgment readback wired to
  `publication.transition()`.
- **eToro (`app/services/etoro_adapter.py`)** builds a trade request
  shape only — `build_trade_request` structurally refuses any
  `account_mode` other than `"demo"`; there is no code path that could
  accidentally select a real endpoint. No real eToro
  application/credentials exist.
- **CopyFactory (`app/services/copyfactory_close_only.py`)** only
  classifies close-only mode strings a caller already read back from
  the API — it never calls CopyFactory itself.

## PAMM/MAM accounting is simulation-only

`app/services/mam_allocation.py` (largest-remainder integer-unit
allocation, deterministic tie-break) and
`app/services/pamm_accounting.py` (simple no-cashflow high-water-mark
fee, which rejects any interval with a deposit/withdrawal rather than
computing a wrong fee) never talk to a real broker. Per the spec's own
"Fair MAM allocation" section: "production uses the actual contractual
broker rule instead." No `AllocationProgram` persistence layer exists
yet either.

## Stripe billing has no real processor behind it

`app/services/stripe_webhook.py` reimplements Stripe's own public
signature scheme (HMAC-SHA256 over `{timestamp}.{raw_body}`) and is
tested against synthetic, Stripe-shaped payloads — there is no Stripe
SDK call, no real Stripe account, and no webhook endpoint exposed to
the real internet yet. No `Product`/`ProductVersion` persistence layer
beyond the `ProductTier` test-mode price fixtures; no proration, tax,
or coupon handling.

## Email delivery is not wired

The local auth system's verification/password-reset link is shown
directly on the page rather than emailed — no SMTP/SendGrid account
exists in this environment. Anyone who can view the page after
triggering the flow can complete it; this is fine for development but
is not a real delivery channel.

## Portfolio research is bounded to the deterministic half

`app/services/portfolio_research.py` covers candidate-subset
enumeration and the single equal-weight recipe. Explicitly NOT built:
correlation/complementarity statistics, the other three recipes
(inverse-volatility, hierarchical risk parity, constrained minimum-
CVaR), walk-forward/holdout/forward-shadow evaluation, and capacity-
stress computation — all genuinely need real authorized historical
sleeve data this environment doesn't have.

## No real LLM/model provider is called

`app/services/model_gateway.py` implements the permission boundary
(allowed purposes, a hard deny-list, mandatory human review, read-
only-research-only API key roles) but calls no real model provider
anywhere — per the spec's own "omit this dependency when no model
feature is selected" rule.

## No real deployment infrastructure exists

AD-22's "commercial deployments and recovery" screen reports real
environment status but honestly discloses there is no actual
deployment infrastructure behind it yet — see `docs/process/RELEASE.md`.

## A real regression this codebase already found and fixed (for context)

Not currently open, but worth knowing this class of bug is real here:
`64d596d`'s own commit message documents a drawdown peak-tracking
regression left mid-verification by a prior session, which the
existing load-bearing test did not catch (its own true peak happened
to sit immediately before its own true trough). Fixed, and a stronger
regression test added. See `docs/agents/VERIFICATION.md` for why this
matters to every future change, not just this one.

## Where to look for the authoritative, requirement-by-requirement account

- `INTEGRATION_ACCEPTANCE_STATUS.md` (repo root) — every one of the
  integration pack's 40 acceptance cases, marked PASS/PARTIAL/BLOCKED/
  NOT_RUN with cited evidence.
- `docs/12_validation_report.md` / `docs/12_addendum_post_phase12_work.md`
  — the original 13-phase build's own line-by-line audit against all
  114 requirements.
