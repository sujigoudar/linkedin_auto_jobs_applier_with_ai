# Security architecture

signal-copier is a **single-owner** application: one operator's own broker
accounts, routing rules, and positions. It is not a multi-tenant product —
that's the commercial platform's (signal-portfolio-commercial's) job, which
this app talks to only through a narrow, signed export relay (see
"Export delivery to the commercial platform" below). This document describes
the real security mechanisms already implemented in this codebase, each
traced to the module that implements it.

## Owner-session authentication (`app/auth.py`)

Every account/routing/provider/position/close/flatten/backtest/signals/orders
endpoint requires a valid owner session. There is no user database — a single
credential (`OWNER_PASSWORD` or `OWNER_PASSWORD_HASH`, see below) stands for
"the owner."

`POST /auth/login` exchanges that password for a server-side session, stored
in `SignalStore.sessions` (so it's revocable and survives a process restart)
and carried to the browser as:

- an `httponly`, `samesite=strict` session cookie (`scr_session`), and
- a separate CSRF token, returned **once**, only in the login response body.

### Fail-closed, not fail-open

If neither `OWNER_PASSWORD` nor `OWNER_PASSWORD_HASH` is set, or
`SESSION_SECRET` is unset, `app/auth.py`'s `auth_configured()` returns
`False` and `RequireOwner.__call__` raises `503` for **every** request. A
misconfigured deployment is loud and broken, not silently public. Setting
**both** `OWNER_PASSWORD` and `OWNER_PASSWORD_HASH` is also treated as
misconfigured (ambiguous which one is authoritative) and fails the same way.

## CSRF token requirement on all mutating requests

A session cookie alone is not sufficient: browsers attach cookies to any
request to this origin, including ones a malicious third-party page tricks
the browser into making. `RequireOwner.__call__` requires, for every request
whose method is not `GET`/`HEAD`/`OPTIONS`, a matching `X-CSRF-Token` header
compared with `hmac.compare_digest` against the session's stored token.
Missing or mismatched → `403`. Because the CSRF token is never placed in a
cookie, only JavaScript that actually read the login response can supply it
— a cross-site form has no way to obtain it.

## Password storage: legacy plaintext vs. argon2id (C05)

Two mutually exclusive ways to configure the one owner credential:

- **`OWNER_PASSWORD`** (legacy): compared with `hmac.compare_digest`
  (constant-time), the same trust level every other secret in this project
  gets from an environment variable. Its actual value is directly usable by
  anything that can read the process environment (a log dump, a leaked
  `.env`, a config export).
- **`OWNER_PASSWORD_HASH`** (preferred): an argon2id hash generated via
  `pwdlib`'s `PasswordHash.recommended()`. The configured value itself is
  not a directly usable credential even if it leaks. Verified with
  `_password_hasher.verify()`; a malformed/unrecognized hash fails closed
  (returns `False`, logged as an error) rather than raising an
  unhandled 500.

Generate a hash with:

```
python -c "from pwdlib import PasswordHash; print(PasswordHash.recommended().hash('<password>'))"
```

## Credential-epoch session invalidation (SEC-04)

Every session is stamped, at creation, with a fingerprint
(`_credential_epoch()`) derived from `sha256(active_credential + SESSION_SECRET)`.
A session is only honored while its stamp still matches the *current*
fingerprint (`_session_or_none`). Practically: rotating `OWNER_PASSWORD`,
`OWNER_PASSWORD_HASH`, or `SESSION_SECRET` — or switching between the
plaintext and hashed credential — automatically revokes every previously
issued session, with no separate "sign out everywhere" step required
(`SignalStore.delete_all_sessions` remains available for revoking sessions
without rotating any secret).

## Webhook shared-secret validation

The two unauthenticated-until-checked ingress routes (`POST
/webhook/{source_name}`, `POST /sms/twilio`) validate a shared secret before
doing any real work:

- `WEBHOOK_SHARED_SECRET` — compared via `hmac.compare_digest`, same
  constant-time discipline as `OWNER_PASSWORD`. Blank means the route is
  disabled (`503`), never silently open.
- `TWILIO_AUTH_TOKEN` — used to validate Twilio's own request signature.
- WhatsApp's `WHATSAPP_APP_SECRET` validates Meta's `X-Hub-Signature-256`
  header; `WHATSAPP_VERIFY_TOKEN` answers Meta's one-time webhook-setup GET
  handshake.
- `NINJATRADER_WEBHOOK_SECRET` — a plain shared secret checked against an
  `X-NinjaTrader-Secret` header (NinjaScript has no built-in request signing).

A valid signature/secret proves the request transited the claimed platform
with the right credential — it does not by itself authorize *who* is allowed
to send trading instructions. Twilio and WhatsApp additionally require an
explicit allowlist of sender identities (`TWILIO_ALLOWED_FROM_NUMBERS`,
`WHATSAPP_ALLOWED_FROM_NUMBERS`); unset/empty means no sender is authorized
— the same fail-closed pattern used throughout this ingress layer.

## Dual CURRENT+PREVIOUS secret rotation

Two secrets in this codebase support zero-downtime rotation via a
CURRENT+PREVIOUS pair, verified by `app/services/catalog_fit_sim_auth.py`
(and the analogous relay verification):

- `CATALOG_FIT_SIM_SIGNING_SECRET` (current) /
  `CATALOG_FIT_SIM_SIGNING_SECRET_PREVIOUS` (optional, defaults blank).

During a rotation window, requests signed with either the current or the
previous secret are accepted; once every caller has moved to the new secret,
the previous value is cleared. See `docs/security/SECRETS.md` for the
step-by-step procedure.

## HMAC-signed export delivery to the commercial platform

`app/relay_worker.py` posts batches of `export_events` to exactly one
allowlisted destination, `RELAY_INGRESS_URL` — never a generic proxy, never
discovered or overridden at request time. Every batch is signed with
`RELAY_SIGNING_SECRET` (`sign_relay_payload`), which must match the same
value configured on signal-portfolio-commercial's own
`RELAY_SIGNING_SECRET`. A blank `RELAY_INGRESS_URL` disables the relay
outright (`RelayNotConfiguredError`) rather than silently posting nowhere.

Every exported `EXECUTION_APPLIED` envelope carries an explicit,
non-inferred evidence classification (`RELAY_EVIDENCE_CLASS`,
`RELAY_ENVIRONMENT`) — the code never infers "this was really live" from a
broker adapter's name, because a paper-named adapter reused against a real
account would otherwise become a silent mislabel. Both default to the
safest values (`INTERNAL_PAPER` / `LOCAL_SIM`); an operator who genuinely
wants to label evidence as observed-live must change these deliberately.

A **separate** trust boundary — the public catalog's fit-simulation endpoint
(`POST /catalog/providers/{source}/fit-simulation`, called by the commercial
platform on behalf of an anonymous prospect) — is signed with a **different**
secret, `CATALOG_FIT_SIM_SIGNING_SECRET`, deliberately never reused from
`RELAY_SIGNING_SECRET`: a leaked fit-simulation secret must never be usable
to forge a relay export-event batch, and vice versa.

## What this document does not claim

This is a single-process, single-owner application. There is no
role-based-access-control system, no per-endpoint permission matrix beyond
"owner or not," and no cross-account isolation, because there is only one
account (the owner's). See `docs/security/AUTHORIZATION.md` for why that is
an honest design choice, not a gap.
