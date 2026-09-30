#!/usr/bin/env python3
"""Standalone, OFFLINE Twitter/X OAuth 2.0 user-context authorization helper.

============================================================================
RUN THIS YOURSELF, LOCALLY. NEVER INSIDE AN AI-VISIBLE OR SHARED TERMINAL.
============================================================================

This script is the ONLY place in this codebase that performs X's real
3-legged OAuth 2.0 (PKCE) user authorization flow: it opens (or prints)
X's own consent screen URL, and asks YOU to log in and approve access in
YOUR OWN browser, then paste the URL your browser was redirected back to
into THIS terminal. None of your X password, 2FA, or session cookies are
ever seen by this script -- only X's own OAuth redirect, exactly like
"Sign in with X" on any third-party website.

WHY this is a separate script, and why an AI agent must never run it:

  - The consent/approval step happens in YOUR OWN browser, where you are
    (or log in as) the X account whose protected-follow visibility this
    collector needs. An AI agent has no browser session of its own logged
    into your X account and must never ask you to paste your X password,
    2FA code, or any cookie/session value into a chat/session transcript.
  - The ACCESS TOKEN (and, if your app's scopes include `offline.access`,
    the REFRESH TOKEN) this script writes to a file is a BEARER
    CREDENTIAL for your X account's authorized API access -- anyone who
    has it can read whatever your account context can see (including
    protected accounts you follow) via the API, without ever seeing your
    password again. Treat it exactly like a password: never commit it,
    never paste its contents into any chat (including to an AI
    assistant), never put it anywhere this repo's own `.gitignore`/
    `.gitleaks.toml` wouldn't already protect a real secret.

WHAT this script does:

  1. Asks (interactively, in THIS terminal, or via --client-id/--client-
     secret/--redirect-uri/TWITTER_OAUTH2_CLIENT_ID etc.) for the OAuth
     2.0 Client ID (and, for a confidential client, Client Secret) of an
     X Developer Portal "App" you created yourself at
     https://developer.x.com, with a Redirect URI you registered there
     matching --redirect-uri exactly (default: http://localhost:8765/
     callback -- this script never runs an actual listening server; it
     only needs the URI string to match what you registered and what
     your browser gets redirected to).
  2. Builds the authorization URL via tweepy's own `OAuth2UserHandler`
     (the current, documented tweepy mechanism for X's 3-legged OAuth 2.0
     with PKCE -- see app/sources/twitter_user.py's own docstring for the
     exact tweepy 4.17.0 API shape this was verified against) and prints
     it for you to open yourself.
  3. YOU open that URL, log into X (if not already), and approve access.
     X redirects your browser to the Redirect URI with a `code`/`state`
     query string -- since this script runs no server, your browser will
     show a "can't be reached" page at that URL; that's expected. Copy
     the FULL URL from your browser's address bar at that point and
     paste it back into this terminal when prompted.
  4. Exchanges that URL for an access token (and refresh token, if
     `offline.access` was in scope) via tweepy's own `fetch_token`, and
     writes the result to the file YOU choose (`--token-path`) as JSON
     -- never only to stdout, so it isn't left sitting in shell history/
     scrollback.
  5. Prints (never logs to a file) the exact environment variable name
     the deployed `TwitterUserSource` collector reads its access token
     from, and a short reminder of the collector-registration step.

WHAT this script deliberately does NOT do:

  - It never reads TWITTER_USER_<COLLECTOR_ID>_ACCESS_TOKEN or any other
    deployment env var for you -- it only writes a new token file; wiring
    it into the running service's real environment (and registering the
    collector via the API) is a separate, explicit step you do
    afterward, documented in docs/security/TWITTER_USER_CONTEXT.md.
  - It never talks to this repository's own database, API, or any other
    part of signal-copier. It has zero imports from `app.*`.
  - It never refreshes an existing token automatically on a schedule --
    see docs/security/TWITTER_USER_CONTEXT.md for how to re-run this
    script (or use `--refresh-token`) to rotate.

Usage (run locally, in your own terminal):

    pip install tweepy
    python scripts/twitter_user_oauth.py \\
        --client-id YOUR_X_APP_CLIENT_ID \\
        --redirect-uri http://localhost:8765/callback \\
        --scope tweet.read --scope users.read --scope offline.access \\
        --token-path ~/.signal-copier-secrets/twitter_user_somehandle.json \\
        --collector-id somehandle

Then, on the machine that actually RUNS signal-copier (never in an AI
session): set TWITTER_USER_SOMEHANDLE_ACCESS_TOKEN to the `access_token`
value from that JSON file, restart the process, and register the
collector (see docs/security/TWITTER_USER_CONTEXT.md).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--client-id", type=str, default=None,
        help="X Developer Portal OAuth 2.0 Client ID (or set TWITTER_OAUTH2_CLIENT_ID).",
    )
    parser.add_argument(
        "--client-secret", type=str, default=None,
        help="X OAuth 2.0 Client Secret, only if your app is a CONFIDENTIAL client "
        "(or set TWITTER_OAUTH2_CLIENT_SECRET). Public clients (PKCE-only) leave this unset.",
    )
    parser.add_argument(
        "--redirect-uri", type=str, default="http://localhost:8765/callback",
        help="Must exactly match a Redirect URI registered on your X app. This script "
        "never runs a listening server at this address -- you paste the redirected URL back in by hand.",
    )
    parser.add_argument(
        "--scope", action="append", default=None,
        help="An OAuth2 scope to request; repeat for multiple (e.g. --scope tweet.read "
        "--scope users.read --scope offline.access). Defaults to "
        "['tweet.read', 'users.read', 'offline.access'] if omitted.",
    )
    parser.add_argument(
        "--token-path", type=str, default="./twitter_user_token.json",
        help="Where to write the resulting token JSON. Choose a path OUTSIDE this git "
        "repository's working tree, or one your .gitignore already excludes -- this file "
        "is a bearer credential.",
    )
    parser.add_argument(
        "--collector-id", type=str, default=None,
        help="Optional: this collector's id in signal-copier's registry, purely to print the "
        "exact env var name you'll need. This script does not register anything itself.",
    )
    args = parser.parse_args(argv)

    try:
        import tweepy
    except ImportError:
        print("tweepy is not installed in this Python environment. Run:\n    pip install tweepy\nthen re-run this script.", file=sys.stderr)
        return 1

    client_id = args.client_id or os.environ.get("TWITTER_OAUTH2_CLIENT_ID")
    if not client_id:
        client_id = input("X OAuth 2.0 Client ID: ").strip()
    client_secret = args.client_secret or os.environ.get("TWITTER_OAUTH2_CLIENT_SECRET")
    if client_secret is None:
        entered = getpass.getpass(
            "X OAuth 2.0 Client Secret (leave blank if your app is a public/PKCE-only client): "
        ).strip()
        client_secret = entered or None

    scopes = args.scope or ["tweet.read", "users.read", "offline.access"]

    handler = tweepy.OAuth2UserHandler(
        client_id=client_id,
        redirect_uri=args.redirect_uri,
        scope=scopes,
        client_secret=client_secret,
    )

    auth_url = handler.get_authorization_url()
    print(
        "\nOpen this URL in YOUR OWN browser, log into X, and approve access:\n\n"
        f"    {auth_url}\n\n"
        "X will redirect your browser to your --redirect-uri with a code/state query "
        "string. Your browser will likely show a 'this site can't be reached' page at "
        f"that point ({args.redirect_uri}) -- that's expected, this script runs no server. "
        "Copy the FULL URL from your browser's address bar at that point.\n"
    )
    redirected_url = input("Paste the full redirected URL here: ").strip()

    token = handler.fetch_token(redirected_url)

    with open(args.token_path, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)
    os.chmod(args.token_path, 0o600)
    print(f"\nToken written to: {args.token_path} (file permissions set to 0600).")

    env_name = (
        f"TWITTER_USER_{args.collector_id.upper()}_ACCESS_TOKEN" if args.collector_id else
        "TWITTER_USER_<COLLECTOR_ID>_ACCESS_TOKEN"
    )
    print(
        "\nNext steps (never in an AI session -- do these yourself, on the machine that runs "
        "signal-copier):\n"
        f"  1. Set {env_name} to the 'access_token' value inside {args.token_path} in that "
        "machine's real environment (e.g. its .env file, never committed).\n"
        "  2. Restart the signal-copier process so it picks up the new environment.\n"
        "  3. Register (or re-point) the collector via the owner-gated POST /pull-collectors "
        "API, referencing this exact env var name in credential_env_var -- never the token itself.\n"
        "  4. Access tokens issued with the offline.access scope eventually expire; re-run this "
        "script to get a fresh one (or extend it with --refresh-token support if you automate "
        "rotation) -- see docs/security/TWITTER_USER_CONTEXT.md.\n"
        "See docs/security/TWITTER_USER_CONTEXT.md for the full procedure.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
