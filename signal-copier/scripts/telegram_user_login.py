#!/usr/bin/env python3
"""Standalone, OFFLINE Telegram user-account login helper.

============================================================================
RUN THIS YOURSELF, LOCALLY. NEVER INSIDE AN AI-VISIBLE OR SHARED TERMINAL.
============================================================================

This script is the ONLY place in this codebase that performs Telegram's
interactive user-account login (phone number -> the code Telegram sends
you -> your 2FA password, if you have one). It is a plain, standalone
CLI script with no dependency on any other part of this service -- you
run it by hand, on your own machine or a host you personally control and
trust, OUTSIDE of any AI coding session, any shared server, or any
terminal another person or process can read.

WHY this is a separate script, and why an AI agent must never run it:

  - The phone number, the login code Telegram texts/sends you, and your
    2FA password (if set) are all typed directly into THIS terminal by
    YOU. None of it is logged, stored, or forwarded anywhere by this
    script. An AI agent has no phone/SMS/Telegram access of its own and
    must never see, request, or relay any of these three things in a
    chat/session transcript -- doing so would put your real account
    credential in a place (a shared AI session, a log, a chat history)
    it must never be.
  - The FILE this script produces (a Telethon `.session` SQLite file, or
    -- if you choose `--string-session` -- a session-string text file)
    is a BEARER CREDENTIAL for your real Telegram account. Anyone who
    has it can act as you on Telegram (read every chat you're in, send
    messages as you) WITHOUT your phone, your code, or your password --
    treat it EXACTLY like a password. Never commit it to git, never
    paste its contents in any chat (including to an AI assistant), and
    never put it anywhere this repository's own `.gitignore`/
    `.gitleaks.toml` wouldn't already protect a real secret.

WHAT this script does:

  1. Asks (interactively, in THIS terminal) for your Telegram API
     credentials (`api_id`/`api_hash` -- get a free pair, once, from
     https://my.telegram.org/apps if you don't have one yet) unless
     given via `--api-id`/`--api-hash` or the `TELEGRAM_API_ID`/
     `TELEGRAM_API_HASH` environment variables.
  2. Calls Telethon's own `TelegramClient(...).start()` -- THIS is the
     one, sole call in this entire codebase that may prompt for a phone
     number, a login code, and (if enabled) your 2FA password. It does
     so using Telegram's own official login flow; this script does not
     re-implement or intercept any part of it.
  3. Once authorized, writes the resulting session to the path YOU
     choose (`--session-path`, default `./telegram_user_session` --
     Telethon appends `.session`) -- or, with `--string-session`, prints
     a portable session-STRING instead and writes it to a file you name
     (never only to stdout, so it isn't left sitting in shell history/
     scrollback).
  4. Prints (never logs to a file) the exact environment variable name
     the deployed `TelegramUserSource` collector reads its session path
     from, and a short reminder of the collector-registration API call
     that references it.

WHAT this script deliberately does NOT do:

  - It never reads `TELEGRAM_USER_<COLLECTOR_ID>_SESSION_PATH` or any
    other deployment env var for you -- it only writes a new session
    file/string; wiring it into the running service's real environment
    (and registering the collector via the API) is a separate, explicit
    step you do afterward, documented in
    `docs/security/TELEGRAM_USER_LOGIN.md`.
  - It never talks to this repository's own database, API, or any other
    part of signal-copier. It has zero imports from `app.*`. That's
    deliberate: this script's entire job is talking to Telegram, once,
    on your own machine, and nothing else.

Usage (run locally, in your own terminal):

    pip install telethon
    python scripts/telegram_user_login.py \\
        --api-id 123456 --api-hash 0123456789abcdef0123456789abcdef \\
        --session-path ~/.signal-copier/telegram_user_buyalerts

    # Or, for a portable session STRING instead of a .session file
    # (e.g. to paste into a secret manager rather than mount a file):
    python scripts/telegram_user_login.py --string-session \\
        --session-path ~/.signal-copier/telegram_user_buyalerts.string

Then, on the machine that actually RUNS signal-copier (never in an AI
session): set the environment variable this script tells you to (e.g.
`TELEGRAM_USER_BUYALERTS_SESSION_PATH=/path/to/telegram_user_buyalerts`,
plus the global `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`), restart the
process, and register the collector (see
`docs/security/TELEGRAM_USER_LOGIN.md`).
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys


def _prompt_int(prompt: str) -> int:
    while True:
        value = input(prompt).strip()
        try:
            return int(value)
        except ValueError:
            print("Please enter a number.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--api-id",
        type=int,
        default=None,
        help="Telegram API id from https://my.telegram.org/apps (or set TELEGRAM_API_ID)",
    )
    parser.add_argument(
        "--api-hash",
        type=str,
        default=None,
        help="Telegram API hash from https://my.telegram.org/apps (or set TELEGRAM_API_HASH)",
    )
    parser.add_argument(
        "--session-path",
        type=str,
        default="./telegram_user_session",
        help="Where to write the session (a .session file is created at this path, "
        "or -- with --string-session -- a plain text file at this exact path). "
        "Choose a path OUTSIDE this git repository's working tree, or one your "
        ".gitignore already excludes -- this file is a bearer credential.",
    )
    parser.add_argument(
        "--string-session",
        action="store_true",
        help="Write a portable Telethon StringSession to --session-path instead of a .session SQLite file.",
    )
    parser.add_argument(
        "--collector-id",
        type=str,
        default=None,
        help="Optional: this collector's id in signal-copier's registry, purely to print the "
        "exact env var name you'll need (e.g. TELEGRAM_USER_<COLLECTOR_ID>_SESSION_PATH). "
        "This script does not register anything itself.",
    )
    args = parser.parse_args(argv)

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        print(
            "telethon is not installed in this Python environment. Run:\n"
            "    pip install telethon\n"
            "then re-run this script.",
            file=sys.stderr,
        )
        return 1

    api_id = args.api_id or (int(os.environ["TELEGRAM_API_ID"]) if os.environ.get("TELEGRAM_API_ID") else None)
    api_hash = args.api_hash or os.environ.get("TELEGRAM_API_HASH")

    if api_id is None:
        print(
            "Get a free api_id/api_hash pair (once) from https://my.telegram.org/apps if you "
            "don't have one yet.",
        )
        api_id = _prompt_int("Telegram api_id: ")
    if not api_hash:
        api_hash = getpass.getpass("Telegram api_hash: ").strip()

    print(
        "\nAbout to start Telegram's own interactive login. You will be asked for your phone "
        "number, then the login code Telegram sends you, then your 2FA password if you have "
        "one set. Type them directly into THIS terminal -- nothing you type here is logged, "
        "stored, or sent anywhere by this script beyond Telegram's own official login API.\n"
    )

    if args.string_session:
        client = TelegramClient(StringSession(), api_id, api_hash)
    else:
        client = TelegramClient(args.session_path, api_id, api_hash)

    with client:
        # This is Telethon's own official interactive login flow -- the
        # ONE call in this entire codebase allowed to prompt for a phone
        # number / login code / 2FA password. See this module's own
        # docstring for why nothing else may ever do this.
        client.start()
        me = client.get_me()
        print(f"\nLogged in as: {getattr(me, 'username', None) or getattr(me, 'first_name', 'unknown')}")

        if args.string_session:
            session_string = client.session.save()
            with open(args.session_path, "w", encoding="utf-8") as f:
                f.write(session_string)
            os.chmod(args.session_path, 0o600)
            print(f"\nSession STRING written to: {args.session_path} (file permissions set to 0600).")
        else:
            print(f"\nSession file written to: {args.session_path}.session")
            session_file = f"{args.session_path}.session"
            if os.path.exists(session_file):
                os.chmod(session_file, 0o600)

    env_name = (
        f"TELEGRAM_USER_{args.collector_id.upper()}_SESSION_PATH" if args.collector_id else
        "TELEGRAM_USER_<COLLECTOR_ID>_SESSION_PATH"
    )
    print(
        "\nNext steps (never in an AI session -- do these yourself, on the machine that runs "
        "signal-copier):\n"
        f"  1. Set {env_name} to the path above (the --string-session file's path, or the "
        ".session file's path WITHOUT the .session suffix) in that machine's real environment "
        "(e.g. its .env file, never committed).\n"
        "  2. Make sure TELEGRAM_API_ID / TELEGRAM_API_HASH are also set there (same values used "
        "above).\n"
        "  3. Restart the signal-copier process so it picks up the new environment.\n"
        "  4. Register (or re-point) the collector via the owner-gated "
        "POST /telegram-collectors API, referencing this exact env var name in "
        "credential_env_var -- never the session's contents.\n"
        "See docs/security/TELEGRAM_USER_LOGIN.md for the full procedure.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
