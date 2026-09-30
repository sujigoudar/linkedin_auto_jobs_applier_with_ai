# Email collector: setup procedure

Read this before enabling `app/sources/email_source.py`'s `EmailSource` --
the IMAP-based collector for trading-alert email (a provider's
newsletter/alert emails, or a dedicated signal-forwarding mailbox you set
up yourself). There was no email source in this codebase before this
track.

## The one hard rule

**The credential goes straight into an environment variable on the
deployment host -- never pasted in chat, never committed, never stored in
the collector registry itself.**

The `email_collectors` table (`app/email_collectors.py`, `app/db.py`)
stores only `credential_env_var`, the NAME of the environment variable
holding your real IMAP app password -- never the password itself. If
you're working with an AI assistant on wiring this up, it can write/review
the code and help you design the collector registration, but the actual
app-password value is something you generate at your provider and set as
an environment variable yourself, on the machine that runs signal-copier.

## Which mode: IMAP (recommended for everyone) or Gmail API?

This track implements **IMAP with an app-specific password** --
universal, works with any provider that supports IMAP (Gmail, Outlook,
Yahoo, a dedicated signal-forwarding mailbox), needs no new dependency
(stdlib `imaplib`/`email` only). Use this unless you have a specific
reason not to.

### Gmail API (OAuth) -- not yet implemented

For a Gmail-only deployment, Google's own OAuth-based Gmail API (avoiding
app-password/"less secure app" friction, with `watch()` + Pub/Sub push or
`historyId`-cursor incremental polling) is a real, better long-term
option -- but it is a substantially larger, separate piece of work (an
OAuth consent flow, token storage/refresh, a new Google API client
dependency, and either a Pub/Sub subscription or a `historyId` cursor
model completely different from IMAP's UID checkpoint). Rather than ship
a rushed half-implementation of it alongside IMAP, this track leaves it
as a documented, flagged follow-up:
`app.email_collectors.ConnectionMode.GMAIL_API` is a real, named enum
value an operator can register a mailbox against for future intent, but
`EmailSource.__init__` raises `NotImplementedError` immediately if
constructed with that mode. Use IMAP today even for a Gmail mailbox --
Gmail supports IMAP with an app password natively (see below).

## Step 1 -- create a dedicated mailbox (recommended, not required)

You can point this at your own primary inbox, but a dedicated mailbox
(e.g. `yourname+signals@gmail.com`, or a free mailbox created just for
this) that only the provider's alert emails ever reach is simpler to
reason about: `sender_allowlist` (below) narrows this collector to
specific sender addresses regardless, but a dedicated mailbox means a
misconfigured allowlist fails safe (empty, not leaky) rather than
potentially exposing unrelated personal mail to this collector's polling.

## Step 2 -- generate an app-specific password

**Gmail:**

1. Enable 2-Step Verification on the Google account, if not already on:
   <https://myaccount.google.com/security>.
2. Go to <https://myaccount.google.com/apppasswords>, create a new app
   password (any name, e.g. "signal-copier"), and copy the 16-character
   password shown. Google shows it exactly once.
3. IMAP host: `imap.gmail.com`, port `993` (SSL). Make sure IMAP access
   is enabled for the account: Gmail Settings -> "Forwarding and
   POP/IMAP" -> IMAP Access -> Enable.

**Outlook / Microsoft 365:**

1. Enable 2-factor authentication on the Microsoft account, if not
   already on.
2. Go to <https://account.microsoft.com/security> -> "Advanced security
   options" -> "App passwords" (or, for a Microsoft 365 org account, your
   organization's own equivalent -- some tenants disable IMAP entirely;
   check with your admin if login fails).
3. IMAP host: `outlook.office365.com`, port `993` (SSL).

**Any other IMAP provider:** consult that provider's own documentation
for "app password" or "IMAP access" -- the mechanism (a long random
per-application password, distinct from your real account password) is
essentially the same everywhere IMAP is offered.

**Treat this password exactly like your account password.** Anyone who
has it can read every email in whatever folder this collector is pointed
at (not send mail, or access anything else in the account, if the
provider scopes app passwords to IMAP-only access -- check your
provider's own documentation for exactly what an app password can do).

- Never commit it, paste it into a chat/issue/support ticket, or store it
  anywhere but the environment variable below.
- If you ever suspect it's leaked, revoke it at the provider (same page
  you generated it from) and generate a fresh one -- this immediately
  invalidates the old value everywhere, with no code change needed here.

## Step 3 -- wire the password into the deployment's environment

On the machine/container that actually **runs** signal-copier, set:

| Env var | Value |
|---|---|
| `EMAIL_<COLLECTOR_ID>_APP_PASSWORD` | The app password from Step 2 -- `<COLLECTOR_ID>` is whatever id you'll register the collector under (upper-cased; e.g. a collector id `buyalerts` reads `EMAIL_BUYALERTS_APP_PASSWORD`). |
| `EMAIL_<COLLECTOR_ID>_USERNAME` | The mailbox's own login username -- usually the full email address (e.g. `alerts-watcher@gmail.com`). |

Restart the process so it picks up the new environment. See
`docs/security/SECRETS.md` for this codebase's general per-collector env
var namespacing convention.

## Step 4 -- register the collector

Non-secret fields (host, folder, sender allowlist, provider mapping) are
recorded in the `email_collectors` registry. `credential_env_var` names
the environment variable from Step 3 -- never the password itself:

```json
{
  "id": "buyalerts",
  "connection_mode": "imap",
  "identity_ref": "alerts-watcher@gmail.com",
  "credential_env_var": "EMAIL_BUYALERTS_APP_PASSWORD",
  "imap_host": "imap.gmail.com",
  "imap_port": 993,
  "imap_folder": "INBOX",
  "sender_allowlist": ["alerts@buyalerts-provider.com"],
  "provider_name": "buyalerts"
}
```

Note what is and isn't in this payload: `identity_ref` is the mailbox
address -- a non-secret identity reference, not a credential.
`credential_env_var` is the NAME of the environment variable holding the
real app password. `sender_allowlist` is **required and must name at
least one real sender address** -- this collector filters to exactly
those senders rather than parsing every email that ever lands in the
mailbox (a provider's alert emails typically come from one or a few known
addresses). Add `subject_patterns` (a JSON array of substrings) if you
need to narrow further within an allow-listed sender (e.g. that sender
also sends unrelated account-notice email).

`allowed_uses` defaults to `["private_trading"]` only -- registering a
collector here makes its signals reach THIS deployment's own engine/
routing. It does **not**, by itself, make this provider eligible for
`signal-portfolio-commercial`'s customer-facing publication pipeline.

## What this collector can and can't do

- **Ingestion**: once registered and started, it polls the configured
  IMAP folder on an interval (`poll_interval_seconds`, default 60s --
  see `app/sources/email_source.py`'s own module docstring for why this
  is a polling interval rather than IMAP IDLE push, and the latency
  tradeoff that implies: a signal can sit in the mailbox up to that many
  seconds before this collector observes it). Only messages from
  `sender_allowlist` (and, if configured, matching `subject_patterns`)
  are ever parsed.
- **Subject + body**: many trading-alert emails put the key instruction
  in the subject line and the SL/TP detail in the body, or vice versa --
  this collector concatenates subject and body into one string before
  parsing, so an instruction spanning both is still recognized.
- **Plain text, HTML, or both**: a plain-text body is preferred when
  present; an HTML-only body has its tags stripped to readable text
  (stdlib-only, no new dependency). An email with no extractable text at
  all (e.g. an image-only alert, no caption) is recorded as an explicit
  "unsupported format" event, never silently dropped.
- **Message-ID identity**: `Signal.message_id` is the email's own native
  `Message-ID` header (RFC 5322 guarantees this is globally unique) --
  used directly, never re-invented. An email is never "edited" the way a
  Telegram/Slack message can be, so a genuinely new email is never
  recorded as a revision of anything. If a provider sends a genuine
  follow-up correction (a new `Message-ID`, referencing the original via
  the standard `In-Reply-To`/`References` headers), that relationship is
  captured on the source ledger (`SourceEvent.parent_message_id`) -- it
  is still treated as a real, new, tradeable instruction, not silently
  merged into or discarded as a duplicate of the original.
- **Historical backlog**: registering a collector does not replay a
  mailbox's history as live trading signals. An explicit, separate,
  read-only "import history" action populates the source ledger for
  research/backtesting only -- it can never itself place a live order.
  Only messages that arrive after this collector's own IMAP-UID
  checkpoint are ever passed to the trading engine.
- **Live trading**: exactly like every other source in this codebase, a
  signal this collector ingests still has to pass this deployment's own
  live-routing qualification gate (`app/qualification.py`,
  `SignalStore.is_route_release_approved`) before any REAL order is ever
  submitted. Registering and starting this collector does not itself
  qualify any route for live trading.

## Rotating or revoking

To revoke: delete/revoke the app password at your provider's security
settings page (the same page you generated it from in Step 2). This takes
effect immediately -- this collector's next poll fails with
`no_mailbox_access` until a fresh password is set and the process is
restarted. To rotate: generate a new app password, update
`EMAIL_<COLLECTOR_ID>_APP_PASSWORD`, restart.

## `UIDVALIDITY` note

IMAP UIDs (this collector's live-admission checkpoint) are only
guaranteed monotonically increasing within one folder for a given
`UIDVALIDITY` epoch (RFC 3501). If your provider ever reports a new
`UIDVALIDITY` for the folder WHILE this collector's process is running
(rare -- e.g. the folder was recreated), it fails closed
(`no_mailbox_access`) rather than silently reinterpreting a stale
checkpoint against the new epoch. This guard is in-memory only, not
persisted to the registry: if the change happens while the process is
*down*, the next restart simply re-baselines and the already-persisted
`checkpoint_uid` could in principle refer to a different message than
before. `UIDVALIDITY` changes are rare in practice, but if you ever
suspect one happened, clear this collector's checkpoint (re-register it)
before trusting live admission again.
