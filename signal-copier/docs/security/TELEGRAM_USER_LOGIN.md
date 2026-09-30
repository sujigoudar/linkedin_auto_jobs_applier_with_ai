# Telegram user-account collector: authorization procedure

Read this before enabling `app/sources/telegram_user.py`'s
`TelegramUserSource` -- the Telethon-based collector for a Telegram
channel you (the account owner) subscribe to personally but cannot add a
bot into (the provider doesn't allow it, or the channel has protected
content / forwarding disabled). If a bot CAN be added instead, prefer the
existing `app/sources/telegram.py` (`TelegramSource`) -- it needs no
personal account credential at all. This document is only for the
channels where that isn't possible.

## The one hard rule

**Authorization happens on your own machine, in your own terminal,
outside of any AI session -- never inside one.**

`scripts/telegram_user_login.py` is a standalone script with zero
dependency on the rest of this codebase (no `app.*` imports). It is the
ONLY place in this repository that performs Telegram's interactive login
(phone number -> login code -> 2FA password). An AI agent working in this
repository:

- must **never** run this script for you (it has no phone/SMS/Telegram
  access, and even if it did, must not see your login code or password).
- must **never** ask you to paste your phone number, login code, 2FA
  password, or the resulting session file's contents into a chat/session.
- must **never** attempt to extract a session from another application
  or device (e.g. copying Telegram Desktop's own local session files) --
  every session this service uses must come from a fresh, deliberate run
  of `scripts/telegram_user_login.py` by you.

If you're working with an AI assistant on wiring this feature up, it can
write/review the code, help you design the collector registration, and
explain what to do -- but the actual `python scripts/telegram_user_login.py`
invocation is something you run yourself, watching the prompts, typing
your own phone/code/password into your own terminal.

## Step 1 -- get a Telegram API id/hash (once)

Go to <https://my.telegram.org/apps>, log in with your own phone number,
and create an "app" (any name/description is fine -- this is just how
Telegram issues API credentials to MTProto clients like Telethon). You'll
get an `api_id` (a number) and an `api_hash` (a hex string). These
identify the CLIENT LIBRARY, not your account credential by themselves --
but treat them as sensitive too (don't publish them).

## Step 2 -- run the login script yourself, locally

```bash
pip install telethon
python scripts/telegram_user_login.py \
    --api-id 123456 \
    --api-hash 0123456789abcdef0123456789abcdef \
    --session-path ~/.signal-copier-secrets/telegram_user_buyalerts \
    --collector-id buyalerts
```

You'll be prompted for your phone number, then the code Telegram sends
you (by SMS or in another logged-in Telegram client), then your 2FA
password if you have one set. All of that happens inside Telethon's own
official login flow (`TelegramClient.start()`) -- this script does not
intercept, log, or store any of it beyond what Telethon itself needs to
complete the login.

On success, you get a `.session` file at the path you chose (e.g.
`~/.signal-copier-secrets/telegram_user_buyalerts.session`). Prefer
`--string-session` instead if you'd rather store a portable session
string in a secret manager than mount a file:

```bash
python scripts/telegram_user_login.py --string-session \
    --api-id 123456 --api-hash 0123456789abcdef0123456789abcdef \
    --session-path ~/.signal-copier-secrets/telegram_user_buyalerts.string \
    --collector-id buyalerts
```

**Treat the resulting file exactly like a password.** Anyone who has it
can act as your Telegram account (read your chats, send messages as you)
without ever seeing your phone, code, or 2FA password again. Specifically:

- Never commit it. Put it outside this repository's working tree (as in
  the examples above), or somewhere your deployment's own `.gitignore`
  already excludes.
- Never paste its contents into a chat, an issue, a support ticket, or
  an AI session.
- Store it only at the path your deployment's
  `TELEGRAM_USER_<COLLECTOR_ID>_SESSION_PATH` environment variable names
  (see below) -- the one place the deployed collector reads it from.

## Step 3 -- wire the session into the deployment's environment

On the machine/container that actually **runs** signal-copier (not in an
AI session), set:

| Env var | Value |
|---|---|
| `TELEGRAM_API_ID` | The same `api_id` used above (global -- one Telegram API app per deployment is fine even with several user-account collectors). |
| `TELEGRAM_API_HASH` | The same `api_hash` used above. |
| `TELEGRAM_USER_<COLLECTOR_ID>_SESSION_PATH` | The exact path (or, for `--string-session`, the file holding the string) from Step 2 -- `<COLLECTOR_ID>` is whatever id you'll register the collector under (upper-cased; e.g. a collector id `buyalerts` reads `TELEGRAM_USER_BUYALERTS_SESSION_PATH`). |

Restart the process so it picks up the new environment. See
`docs/security/SECRETS.md` for this codebase's general per-collector env
var namespacing convention (the same `<PROVIDER>_<ID>_<FIELD>` pattern
every broker adapter already uses).

## Step 4 -- register the collector

Register it via the owner-gated registry API (`POST
/telegram-collectors`, owner session required -- see
`docs/security/AUTHORIZATION.md`):

```json
{
  "id": "buyalerts",
  "connection_mode": "user_account",
  "identity_ref": "+1XXXXXXXXXX",
  "credential_env_var": "TELEGRAM_USER_BUYALERTS_SESSION_PATH",
  "chat_id": "-1001234567890",
  "provider_name": "buyalerts"
}
```

Note what is and isn't in this payload: `identity_ref` is your own
phone number or username -- a non-secret identity reference, not a
credential. `credential_env_var` is the NAME of the environment variable
holding the real session path -- never the session itself. The registry
(`telegram_collectors` table, `app/telegram_collectors.py`) never stores
a token, session string, or password value in any column, by design (see
that module's docstring).

`allowed_uses` defaults to `["private_trading"]` only -- registering a
collector here makes its signals reach THIS deployment's own engine/
routing. It does **not**, by itself, make this provider eligible for
`signal-portfolio-commercial`'s customer-facing publication pipeline;
that is a completely separate system with its own `RightsGrant`
authorization, in a separate database this registry never touches.

## What this collector can and can't do

- **Ingestion**: once authorized, it observes new messages
  (`events.NewMessage`), edits (`events.MessageEdited`), and -- for a
  group/channel/supergroup -- deletions (`events.MessageDeleted`) in the
  configured chat, the same way any Telegram client you're logged into
  would. Text and photo/document captions are both read (Telegram uses
  the same message field for both); a message with neither (e.g. a
  sticker or voice note with no caption) is recorded as an explicit
  "unsupported format" event, never silently dropped.
- **Protected-content (`noforwards`) chats**: this collector reads and
  records that flag as evidence for downstream forwarding/redistribution
  policy. It does not, and cannot, use that flag to block or bypass
  anything about how it ingests messages -- see
  `app/sources/telegram_user.py`'s module docstring for the documented
  (not empirically re-verified against a real Telegram account in this
  build) distinction between raw ingestion and the UI-level forward/save
  restriction that flag actually governs.
- **Historical backlog**: a fresh authorization does not replay a
  channel's history as live trading signals. An explicit, separate,
  read-only "import history" action populates the source ledger for
  research/backtesting only -- it can never itself place a live order
  (see `TelegramUserSource.import_history`'s own docstring). Only
  messages that arrive live, after this collector's own checkpoint, are
  ever passed to the trading engine.
- **Live trading**: exactly like every other source in this codebase, a
  signal this collector ingests still has to pass this deployment's own
  live-routing qualification gate (`app/qualification.py`,
  `SignalStore.is_route_release_approved`) before any REAL order is ever
  submitted on any account. Registering and authorizing this collector
  does not itself qualify any route for live trading.

## Rotating or revoking

To revoke access entirely: log into Telegram (any client) -> Settings ->
Devices, and terminate the session named after this API app. The
`.session` file on disk becomes useless immediately. To rotate to a fresh
session, just re-run `scripts/telegram_user_login.py` (yourself, locally)
and point `TELEGRAM_USER_<COLLECTOR_ID>_SESSION_PATH` at the new file.
