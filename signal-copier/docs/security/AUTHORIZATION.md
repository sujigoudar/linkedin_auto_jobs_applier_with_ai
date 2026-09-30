# Authorization model

**This app has exactly one authorization level: owner or not-owner.** There
is no role-based access control, no read-only role, no secondary approver,
and no per-account or per-endpoint permission matrix. This document states
that plainly because it's easy to assume a trading system has a richer
authorization model than it does, and building documentation around an
imagined RBAC system would be actively misleading.

## Why this is a deliberate design choice, not an oversight

signal-copier is, by its own architecture, a **single-owner** application:
one operator's own broker accounts, one routing configuration, one set of
destination accounts (`app/config.py`'s `MAX_OWNER_NOTIONAL_EXPOSURE`
comment: "this service is single-tenant — one RoutingConfig, one set of
destination accounts, one owner"). A role system — "trader" vs. "viewer" vs.
"admin" — implies multiple distinct humans with different levels of trust
over the same account. That doesn't exist here. If a second person needs
supervised, permissioned access to trading activity across multiple
owners/accounts, that's the commercial platform's job
(signal-portfolio-commercial), which this app feeds through the signed
export relay — see `docs/security/ARCHITECTURE.md`.

## What actually gates access

`app/auth.py`'s `RequireOwner` dependency is the single authorization
check used throughout `app/main.py`. Every route either:

1. Requires no authentication at all — `GET /health` only (deliberately
   minimal and public: liveness plus whether background workers are making
   progress, no account IDs or balances).
2. Requires `RequireOwner()` (the default, `require_csrf=True`) — every
   mutating endpoint and most read endpoints.
3. Requires `RequireOwner(require_csrf=False)` (used as `require_owner_read`)
   for a small number of read-only, owner-gated endpoints (e.g.
   `/system/readiness`, `/metrics`) where CSRF protection is unnecessary
   because nothing is mutated — but a valid owner session is still required,
   since these expose operational detail (pending-order counts, protection
   deficits, account-level readiness) an anonymous caller has no business
   reading.

There is no third tier. A valid session either belongs to the owner, or the
request is rejected with `401`/`403`/`503`.

## Ingress sources are not "authorization," they're transport authenticity

Webhook/SMS/WhatsApp/NinjaTrader sources validate a shared secret or
platform signature (see `docs/security/ARCHITECTURE.md`). That proves the
request transited the claimed platform with the right credential — it is
**not** the owner-authorization model above, and it does not grant access
to any owner-gated endpoint. A validated webhook signal can only ever
become routed trading activity through the existing routing configuration;
it cannot read positions, change settings, or authenticate as the owner.

## What a future multi-user model would require (not built here)

If this app ever needed to support more than one authorized human, it would
need, at minimum: a real user/credential table (not a single env-var
credential), per-user session scoping, a permission model distinguishing
at least "can view" from "can place/close/flatten," and an audit trail
attributing each mutating action to a specific user rather than "the
owner." None of that exists today. Documenting a role system that isn't
implemented would misrepresent the actual security boundary this app
enforces — which is exactly "the one person who has the owner credential,
full stop."
