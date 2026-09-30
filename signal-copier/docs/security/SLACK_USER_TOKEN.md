# Slack user-token collector: authorization procedure

Read this before enabling `app/sources/slack_user.py`'s `SlackUserSource`
-- an adapter for a Slack workspace you (the account owner) are a member
of but **cannot invite a bot into** (you don't administer the workspace,
or the admins have disabled app installs). If a bot CAN be added
instead, prefer the existing `app/sources/slack.py` (`SlackSource`) -- it
needs no personal OAuth user token at all. This document is only for the
channels where that isn't possible.

This uses Slack's own, officially-documented **OAuth user token**
mechanism (`xoxp-...`) -- an app acting AS the authenticated person, with
scopes granted to that person specifically, not a bot the workspace
owner must separately invite into a channel. This is a standard,
ToS-compliant Slack feature (Slack's own OAuth documentation calls this
a "user token" as distinct from a "bot token"), not a workaround.

## The one hard rule

**The OAuth consent step happens in your own browser, on Slack's own
`slack.com` domain -- never inside an AI session.** An AI assistant can
write/review the code and explain what to do, but the actual "Install to
Workspace" click and Slack login/approval is something you do yourself.
It must never ask you to paste your Slack password, a 2FA code, or the
resulting token's value into a chat/session.

## Step 1 -- create a Slack app (once)

1. Go to <https://api.slack.com/apps> and click "Create New App" ->
   "From scratch". Any name is fine (e.g. "signal-copier user
   collector") -- pick a workspace you're a member of (this does NOT
   require you to be a workspace admin; Slack app creation is per-user).
2. Under **Socket Mode** (left sidebar), enable it. This generates an
   **app-level token** (`xapp-...`) with the `connections:write` scope
   -- copy it; this is the token `SLACK_USER_APP_TOKEN` reads (global,
   shared across every user-token collector this deployment runs against
   apps you create the same way).
3. Under **OAuth & Permissions** (left sidebar), scroll to **User Token
   Scopes** (NOT "Bot Token Scopes" -- this is the important distinction
   from the existing bot-based `SlackSource` setup) and add:
   - `channels:history` (read messages in public channels you're in)
   - `groups:history` (read messages in private channels you're in, if
     the channel you need is private)
   - `channels:read` / `groups:read` (resolve channel info)
4. Under **Event Subscriptions**, enable events and subscribe to the
   `message.channels` (and, for a private channel, `message.groups`)
   **user** event (Slack's Event Subscriptions page lists events
   separately for bot vs. user token authorization -- pick the ones
   under "Subscribe to events on behalf of users").
5. Scroll to the top of the **OAuth & Permissions** page and click
   **"Install to Workspace"** (or "Install to \<workspace name\>"). This
   is Slack's own OAuth install flow -- it happens entirely on
   `slack.com`'s own consent screen in your browser; there is no
   redirect URI to configure for this kind of same-page install. Approve
   the requested scopes.
6. After approving, the same page shows a **"User OAuth Token"** starting
   `xoxp-...`. This is the real credential -- treat it exactly like a
   password (see below).

## Step 2 -- treat the token like a password

**Never commit it. Never paste its contents into a chat, an issue, a
support ticket, or an AI session.** Store it only at the path your
deployment's `SLACK_USER_<COLLECTOR_ID>_TOKEN` environment variable
names (see Step 3) -- the one place the deployed collector reads it
from. Anyone who has this token can act as you within the scopes you
granted (read the channels you're in) without ever seeing your Slack
password or 2FA code again.

## Step 3 -- wire the token into the deployment's environment

On the machine/container that actually **runs** signal-copier (not in an
AI session), set:

| Env var | Value |
|---|---|
| `SLACK_USER_APP_TOKEN` | The app-level token (`xapp-...`) from Step 1.2 -- global, shared across every user-token collector. |
| `SLACK_USER_<COLLECTOR_ID>_TOKEN` | The user token (`xoxp-...`) from Step 1.6 -- `<COLLECTOR_ID>` is whatever id you'll register the collector under (upper-cased; e.g. a collector id `buyalerts` reads `SLACK_USER_BUYALERTS_TOKEN`). |

Restart the process so it picks up the new environment.

## Step 4 -- register the collector

Register it via the owner-gated registry API (`POST /pull-collectors`,
owner session required -- see `docs/security/AUTHORIZATION.md`):

```json
{
  "id": "slack-buyalerts",
  "provider": "slack",
  "auth_mode": "oauth_user_token",
  "identity_ref": "U0123ABC",
  "credential_env_var": "SLACK_USER_BUYALERTS_TOKEN",
  "target_id": "C0123ABC",
  "provider_name": "buyalerts"
}
```

Note what is and isn't in this payload: `identity_ref` is your own
Slack user id (or display name) -- a non-secret identity reference, not
a credential. `target_id` is the channel id (right click the channel ->
"View channel details" to find it). `credential_env_var` is the NAME of
the environment variable holding the real user token -- never the token
itself. The registry (`pull_collectors` table,
`app/collector_registry.py`) never stores a token value in any column.

`allowed_uses` defaults to `["private_trading"]` only -- registering a
collector here makes its signals reach THIS deployment's own
engine/routing. It does **not**, by itself, make this provider eligible
for `signal-portfolio-commercial`'s customer-facing publication
pipeline; that is a completely separate system this registry never
touches.

## What this collector can and can't do

- **Ingestion**: once installed and registered, it observes new messages
  in the configured channel via Slack's Events API over Socket Mode
  (the SAME real-time transport `SlackSource` already uses, just
  authenticated as your user token instead of a bot token), for any
  channel your account is a member of -- no bot invite needed.
- **Edits**: Slack delivers a distinct `subtype: "message_changed"`
  event (https://api.slack.com/events/message/message_changed) carrying
  the edited message's own `edited.ts` marker -- this collector reads
  that as a real revision, surfaced as an EDIT source event, never
  checkpoint-gated (an edit to an old message is still real new
  activity).
- **Deletions**: Slack delivers a distinct `subtype: "message_deleted"`
  event (https://api.slack.com/events/message/message_deleted) for any
  channel the connected app can see. This IS a real, documented Slack
  Events API event, reasoned from Slack's own published API reference --
  not empirically re-confirmed against a live workspace in this build
  (there's no real Slack workspace available in the environment this
  adapter was written in). If real-world use ever shows a workspace-level
  setting suppresses this delivery, that's the kind of thing this
  collector's `health_detail` is meant to record honestly.
- **A message with no text** (e.g. a file/image upload with no caption)
  is recorded as an explicit "unsupported format" event, never silently
  dropped.
- **Historical backlog**: a fresh registration does not replay a
  channel's history as live trading signals. An explicit, separate,
  read-only "import history" action (using `conversations.history`)
  populates the source ledger for research/backtesting only -- it can
  never itself place a live order. Only messages that arrive live, after
  this collector's own checkpoint, are ever passed to the trading
  engine.
- **Live trading**: exactly like every other source in this codebase, a
  signal this collector ingests still has to pass this deployment's own
  live-routing qualification gate (`app/qualification.py`,
  `SignalStore.is_route_release_approved`) before any REAL order is ever
  submitted on any account. Registering and authorizing this collector
  does not itself qualify any route for live trading.

## Rotating or revoking

To revoke access entirely: go to <https://api.slack.com/apps>, select
the app, and either "Uninstall App" (Settings -> Install App) or delete
the app outright -- the `xoxp-...` token becomes invalid immediately. To
rotate, reinstall the app (Step 1.5) to get a fresh token, and update
`SLACK_USER_<COLLECTOR_ID>_TOKEN` to the new value.
