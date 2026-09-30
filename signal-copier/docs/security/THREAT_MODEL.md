# Threat model

This is a working threat model for a real, single-owner algorithmic-trading
relay, not a compliance checklist. Every mitigation named here is traced to
the code that implements it; every gap named here is real and currently
unmitigated, stated honestly rather than glossed over.

## What's actually at risk (assets)

1. **Broker credentials.** API keys/secrets/tokens for every configured
   destination account (Alpaca, ccxt exchanges, Schwab, Tastytrade,
   TradeStation, Tradovate, IBKR host/port, MT4/MT5 login, MetaApi token,
   Rithmic credentials, NinjaTrader relay URL, SignalStack webhook URLs).
   Compromise means an attacker can place real orders on the owner's real
   accounts.
2. **The owner session.** Whoever holds a valid `scr_session` cookie +
   matching CSRF token can issue every mutating command this app exposes:
   close positions, flatten accounts, change routing, alter risk limits.
3. **Financial commands themselves.** Entry/exit/stop/target/replace/cancel
   instructions reaching a broker — the actual attack surface this whole
   app exists to gate.
4. **Ingress signal sources.** Webhook, SMS, WhatsApp, Telegram, Discord,
   Slack, Twitter, NinjaTrader — anything that can inject a "signal" this
   app might route to a broker.
5. **Exported evidence to the commercial platform.** `export_events` rows
   describe real (or paper) executions; a forged or replayed batch could
   corrupt downstream evidence used for billing/provider-value decisions.
6. **The `command_ledger` and `capital_reservations` durable state** —
   the record of "what this process believes it has done or committed to."
   Corruption or loss here degrades the operator's ability to reconcile
   after an incident.

## Mitigations already in place

### Ingress abuse (webhook / SMS)

`app/rate_limit.py` rate-limits `POST /webhook/{source}` and `POST
/sms/twilio` to 30 requests/minute per source IP (slowapi, in-memory,
per-process — see "Scope of the rate limiter" below). This exists because
each request costs real work (JSON/form parsing, a constant-time secret
compare, a store lookup for idempotency) even before being rejected as
invalid — an unrestrained flood can burn CPU/DB connections purely through
rejected requests. The catalog fit-simulation endpoint is separately
rate-limited (20/minute) because each accepted request there triggers a
real `BacktestEngine` replay.

**Scope of the rate limiter is honestly bounded**: it is per-process,
in-memory, keyed by client IP. It is the right scope for how this app
actually runs today (one process, one writer). A deployment with multiple
processes behind a shared load balancer would need a shared backend
(slowapi's `storage_uri`, e.g. Redis) — not implemented, since nothing in
this project runs that way.

### Owner authentication and session hijacking

- Fail-closed auth (`app/auth.py`): unconfigured auth returns `503` for
  every route, never silently opens them. See `docs/security/ARCHITECTURE.md`.
- CSRF protection on every mutating request, independent of the session
  cookie.
- Argon2id password hashing available (`OWNER_PASSWORD_HASH`), so a leaked
  configuration value is not itself a usable credential.
- Constant-time comparisons (`hmac.compare_digest`) everywhere a secret is
  checked against user input — a plain `!=` would leak, via response-time
  variance, how many leading characters of a guessed secret are already
  correct.
- Credential-epoch session invalidation (SEC-04): rotating any owner secret
  revokes every existing session automatically.

### Ingress source authenticity

Every pull/push signal source that can plausibly be spoofed validates a
shared secret or platform-native signature before any signal reaches
routing logic (see `docs/security/ARCHITECTURE.md`'s "Webhook shared-secret
validation"). Sender-level authorization (not just transport authenticity)
is enforced for Twilio and WhatsApp via explicit allowlists.

### Secret rotation

`CATALOG_FIT_SIM_SIGNING_SECRET`/`_PREVIOUS` support a two-step,
zero-downtime rotation so a compromised secret can be replaced without a
window where every in-flight signed request from the not-yet-updated side
is rejected. See `docs/security/SECRETS.md`.

### Cross-process/cross-host writer fencing

`app/writer_lease.py`'s fencing-token mechanism (see `docs/FAILOVER.md`)
guards against a *specific* threat: a second process (a zombie instance,
or a second site brought up without following the manual promotion
procedure) submitting broker commands concurrently with the legitimate
writer. Every command-execution path checks `require_active()` immediately
before a broker write; a stale token is refused instantly, independent of
whether its own lease row still looks unexpired.

### Durable, pre-effect financial-intent recording

`app/command_ledger.py` writes and commits a durable row **before** every
broker call this codebase makes (entry, close, stop, target, replace,
cancel, flatten), so a process death between "decided to submit" and "wrote
the result" leaves a `pending_submission` row a restart/reconciliation pass
can find, instead of leaving no trace at all that a command was attempted.

## Threats this codebase does NOT claim to mitigate (honest gaps)

- **Real broker-sandbox integration testing.** This environment has no
  network egress to live broker/exchange sandboxes (Alpaca paper API,
  IBKR TWS, a real ccxt testnet, etc.). Every broker adapter's behavior
  against the actual, live remote API is unverified by this session's own
  work beyond what each adapter's own docstring discloses having checked.
  Treat any adapter without an explicit "verified against a live sandbox"
  note as code-complete but operationally unqualified.
- **The old-writer-is-actually-dead problem.** `app/writer_lease.py`
  explicitly does not, and cannot, prove a prior process has stopped
  running — only a human-confirmed cloud-provider stop/terminate or a
  revoked brokerage credential does that (`deploy/RUNBOOK.md`). The
  fencing token is a second, automatic layer underneath that manual step,
  never a replacement for it.
- **Split-brain via a shared broker session used outside this app.**
  Fencing this application's own command-execution paths does not revoke a
  brokerage credential some other process or a human could still use
  directly at the broker.
- **Multi-process rate limiting.** As noted above, the in-memory limiter
  does not coordinate across processes; a real horizontally-scaled
  deployment (not how this app runs today) would need a shared backend.
- **Disk exhaustion at the OS level.** `EXPORT_OUTBOX_SIZE_CEILING_BYTES`
  is an application-level alerting threshold on the export outbox
  specifically (see `docs/operations/SLO.md`); actual behavior under real
  OS-level disk exhaustion (SQLite write failures mid-transaction, WAL
  growth, Litestream replication under a full disk) is untested by this
  session's INT-040 work and remains an open gap — see
  `docs/operations/DR.md`.
- **Robinhood and Schwab have no sandbox at all.** Both adapters are
  explicitly gated behind a separate acknowledgement flag
  (`ROBINHOOD_ACKNOWLEDGE_TOS_RISK`, `SCHWAB_ACKNOWLEDGE_NO_SANDBOX`)
  precisely because there is no safe way to test against them short of
  real money — this is disclosed risk, not a mitigated one.
- **No multi-user authorization model.** See
  `docs/security/AUTHORIZATION.md` — by design, not oversight, but it means
  this app has no answer to "what if the owner's own credential is
  compromised" beyond session revocation and secret rotation; there is no
  secondary approver, no read-only role, no audit-trail-with-attribution
  beyond "the owner did it."
- **XSS/accessibility findings with disclosed, not-yet-fixed scope.** See
  README's "Security notes for when this goes live" (C33/C34): two
  accessibility violations (`aria-required-children`, `color-contrast`)
  remain open, disclosed baseline issues, not security vulnerabilities in
  the traditional sense but noted here for completeness since they affect
  the dashboard's trustworthiness as a control surface.
