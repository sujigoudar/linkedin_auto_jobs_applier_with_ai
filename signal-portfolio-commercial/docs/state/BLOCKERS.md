# Blockers

Real, currently-standing blockers — each one either an external
dependency this environment genuinely doesn't have, or a policy denial
that was actually hit, not assumed. See `docs/PENDING_DECISIONS.md` for
the six owner-only action cards these largely funnel into.

## External accounts/credentials that don't exist in this environment

- **No Collective2 sandbox or API4 credentials.**
  `app/services/collective2_publisher.py` builds the Order envelope but
  cannot transmit it. Blocks TASK-01 (real transport) in
  `docs/state/tasks.json`.
- **No eToro application/credentials.** `app/services/etoro_adapter.py`
  is demo-only structurally (refuses any non-`"demo"` `account_mode`
  outright — there is no fallback branch that could accidentally choose
  a real endpoint). Blocks TASK-03.
- **No real Stripe account.** `app/services/stripe_webhook.py`
  reimplements Stripe's own public HMAC-SHA256 signing scheme and
  verifies it against synthetic payloads only — no Stripe SDK call
  exists anywhere in this codebase. Blocks TASK-04.
- **No SMTP/SendGrid account.** The local auth system (`e303af9`,
  ID-01/ID-02/ID-03) shows the email-verification/password-reset link
  directly on the page instead of sending it, the same disclosed-gap
  pattern used elsewhere.
- **No real broker integration for PAMM/MAM.** Accounting
  (`pamm_accounting.py`, `mam_allocation.py`) is simulation-only by the
  spec's own explicit scope boundary — "production uses the actual
  contractual broker rule instead."
- **No real licensed historical market/sleeve data.** Blocks the
  correlation/complementarity statistics and the three non-default
  portfolio-research recipes (TASK-06) — fabricating this data would
  violate the build's own "never generate fabricated returns" rule.

## A real, confirmed policy denial (not a transient failure)

- **Collective2 API4 documentation fetch was refused by this
  environment's egress policy.** This session (during the original
  build) tried `WebFetch` on `https://collective2.com/apidoc/v4` to
  resolve a real documentation conflict — the docs document TIF0=day/
  1=GTC, but one conditional example uses TIF value 2 — and the
  request was refused because `collective2.com` is not on this
  environment's allowed domain list, confirmed via
  `/root/.ccr/README.md` as an organization policy denial, not a
  transient network failure. Per that file's own rule, such denials
  are reported and not retried or routed around.
  **Resolution**: `Tif` in `app/services/collective2_publisher.py`
  defines only the two undisputed values (`DAY=0`, `GTC=1`);
  `build_order` raises `UnsupportedTifError` rather than accepting or
  silently normalizing the disputed value 2. This stays BLOCKED (see
  TASK-02) until real, captured vendor confirmation exists — do not
  widen the enum on inference, and do not re-attempt
  `collective2.com` from this environment without the egress policy
  itself changing.

## Owner-only decisions (not code blockers, but still blocking)

Six standing action cards cannot be advanced by more coding — see
`docs/PENDING_DECISIONS.md` for the full list and what each one gates.

## How to add a new blocker here

Only add a real one: something actually tried and actually denied, or
a real missing credential/account/dataset — never a task simply not
started yet (that belongs in `docs/state/tasks.json` as `open`, not
here as `blocked`). State what was tried, what happened, and exactly
what would unblock it, the same way the Collective2 entry above does.
