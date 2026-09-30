# Twitter/X user-context collector: authorization procedure

Read this before enabling `app/sources/twitter_user.py`'s
`TwitterUserSource` -- an adapter for reading tweets from accounts you
follow that are **protected/private and approved-follower-only**, which
the existing `app/sources/twitter.py` (`TwitterSource`, app-only
`TWITTER_BEARER_TOKEN` filtered stream) can never see, regardless of
whether you personally follow and were approved by that account -- an
app-only bearer token only ever sees PUBLIC tweets matching a
filtered-stream rule. If the accounts you want to copy from are all
public, prefer the existing `TwitterSource` -- it needs no personal OAuth
authorization at all. This document is only for protected accounts.

This uses X's own, officially-documented **OAuth 2.0 user-context**
authorization (3-legged OAuth 2.0 with PKCE) -- API calls are made AS the
authenticated person, seeing exactly what their own account's follow
relationships allow, not a workaround or scraping mechanism.

## The one hard rule

**The OAuth consent step happens in your own browser, on X's own
`x.com`/`twitter.com` domain -- never inside an AI session.** An AI
assistant can write/review the code and explain what to do, but the
actual login/approval step is something you do yourself, and
`scripts/twitter_user_oauth.py` is the one script in this repo that
walks you through it. It must never ask you to paste your X password,
2FA code, or the resulting token's value into a chat/session.

## Step 1 -- create an X Developer Portal app (once)

1. Go to <https://developer.x.com>, apply for/open a developer account if
   you don't already have one, and create a **Project** and an **App**
   inside it.
2. Under the App's **User authentication settings**, enable **OAuth
   2.0**, set the app type (native/public client is simplest -- no
   client secret needed, PKCE-only), and add a **Redirect URI** --
   `http://localhost:8765/callback` works with
   `scripts/twitter_user_oauth.py`'s own default; it must match exactly.
3. Note the **OAuth 2.0 Client ID** shown on that page (and Client Secret
   only if you chose a confidential client type).
4. Decide which scopes you need: `tweet.read` and `users.read` are the
   minimum to read a followed account's tweets; add `offline.access` if
   you want a refresh token (so the access token can be renewed without
   re-running the browser flow every time it expires -- recommended for
   a long-running collector).

## Step 2 -- run the authorization script yourself, locally

```bash
pip install tweepy
python scripts/twitter_user_oauth.py \
    --client-id YOUR_X_APP_CLIENT_ID \
    --redirect-uri http://localhost:8765/callback \
    --scope tweet.read --scope users.read --scope offline.access \
    --token-path ~/.signal-copier-secrets/twitter_user_somehandle.json \
    --collector-id somehandle
```

The script prints an authorization URL. Open it in YOUR OWN browser, log
into X (if needed), and approve access. X redirects your browser to your
`--redirect-uri` with a `code`/`state` query string -- since the script
runs no actual server, your browser will show a "can't be reached" page
at that point; that's expected. Copy the FULL URL from your browser's
address bar and paste it back into the terminal when prompted.

The script writes the resulting `{"access_token": "...", "refresh_token":
"...", ...}` JSON to the path you chose. **Treat this file exactly like
a password** -- see `scripts/twitter_user_oauth.py`'s own docstring for
the full handling rules (never commit it, never paste its contents into
any chat, store it only outside this repo's working tree or somewhere
your `.gitignore` already excludes it).

## Step 3 -- wire the token into the deployment's environment

On the machine/container that actually **runs** signal-copier (not in an
AI session), set:

| Env var | Value |
|---|---|
| `TWITTER_USER_<COLLECTOR_ID>_ACCESS_TOKEN` | The `access_token` value from Step 2's JSON file -- `<COLLECTOR_ID>` is whatever id you'll register the collector under (e.g. `somehandle` reads `TWITTER_USER_SOMEHANDLE_ACCESS_TOKEN`). |

Restart the process so it picks up the new environment. Access tokens
issued with `offline.access` eventually expire (X's own token lifetime,
typically ~2 hours for the access token); re-run
`scripts/twitter_user_oauth.py` to get a fresh one when that happens --
this codebase does not automate refresh-token rotation for you.

## Step 4 -- register the collector

Register it via the owner-gated registry API (`POST /pull-collectors`,
owner session required -- see `docs/security/AUTHORIZATION.md`):

```json
{
  "id": "twitter-somehandle",
  "provider": "twitter",
  "auth_mode": "oauth2_user_context",
  "identity_ref": "@your_own_handle",
  "credential_env_var": "TWITTER_USER_SOMEHANDLE_ACCESS_TOKEN",
  "target_id": "1234567890",
  "provider_name": "somehandle"
}
```

Note what is and isn't in this payload: `identity_ref` is YOUR OWN
handle (the identity whose OAuth context is being used) -- a non-secret
identity reference, not a credential. `target_id` is the numeric X user
id of the PROTECTED account you want to read (X's v2 API is id-keyed;
`GET /2/users/by/username/:username` with the same access token
resolves a handle to an id as a one-time lookup -- not automated by this
codebase). `credential_env_var` is the NAME of the environment variable
holding the real access token -- never the token itself.

## What this collector can and can't do -- verify-not-assume, disclosed

- **Real-time delivery**: X API v2's filtered stream (what
  `TwitterSource` uses) is an App-only (project bearer token) endpoint --
  there is no tweepy or X API mechanism to run it authenticated as a
  specific user. This adapter is therefore **REST-polling-based**
  (`GET /2/users/:id/tweets` on an interval, default 30s, configurable),
  not stream-based. This is a genuine, verified limitation (checked
  against tweepy 4.17.0's own installed source, not assumed from
  memory -- see `app/sources/twitter_user.py`'s own module docstring for
  exactly what was checked), not a corner this adapter cut.
- **Checkpointing**: uses X's own `since_id` parameter on
  `get_users_tweets` -- the native incremental-fetch mechanism the API
  already provides -- as the live-admission checkpoint, so a reconnect
  never re-processes tweets already seen.
- **Edits**: X supports Tweet edits; this adapter reads the v2
  `edit_history_tweet_ids` field (`tweet_fields=["edit_history_tweet_
  ids"]`) to recognize a later revision of an edit chain and surface it
  as an EDIT source event with the original tweet's id preserved. This
  is reasoned from X API v2's own published field reference, not
  empirically re-confirmed against a live edited tweet (no real X
  account is available in the environment this adapter was written in).
- **Deletions are NOT observable** through this polling mechanism -- a
  tweet that stops appearing between two polls could be a real delete, a
  protection-status change, or pagination/rate-limit timing, and this
  adapter has no way to distinguish those. It therefore never emits a
  DELETE source event -- an honest, disclosed gap, never a guessed one.
- **Historical backlog**: a fresh registration does not replay an
  account's history as live trading signals. An explicit, separate,
  read-only "import history" action populates the source ledger for
  research/backtesting only -- it can never itself place a live order.
  Only tweets observed live, after this collector's own `since_id`
  checkpoint, are ever passed to the trading engine.
- **Live trading**: exactly like every other source in this codebase, a
  signal this collector ingests still has to pass this deployment's own
  live-routing qualification gate (`app/qualification.py`,
  `SignalStore.is_route_release_approved`) before any REAL order is ever
  submitted on any account. Registering and authorizing this collector
  does not itself qualify any route for live trading.

## Rotating or revoking

To revoke access entirely: go to <https://x.com/settings/connected_apps>,
find your app, and remove access -- the access/refresh token pair
becomes invalid immediately. To rotate, re-run
`scripts/twitter_user_oauth.py` and update
`TWITTER_USER_<COLLECTOR_ID>_ACCESS_TOKEN` to the new value.
