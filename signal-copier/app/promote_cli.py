"""The ONE deliberate, human-run way a different site ever becomes the
writer -- see docs/FAILOVER.md and app/writer_lease.py's module
docstring for the full model this implements.

No automatic promotion exists anywhere else in this codebase. This
command must be run explicitly, by a human, after independently
completing deploy/RUNBOOK.md's own "Before promotion" checks (confirming
the prior writer's HOST is actually stopped or its brokerage credentials
revoked -- this command's own lease-expiry check is a second, additional,
automatic guard, never a substitute for that manual confirmation).

Usage (see also --help)::

    python -m app.promote_cli status
    python -m app.promote_cli promote \\
        --confirm-old-writer-fenced \\
        --confirm-reconciled \\
        --confirm-identity

`promote` refuses to do anything unless ALL THREE `--confirm-*` flags are
given explicitly -- this is the "explicit new authority" the design
review asked for, expressed as flags a human (or a very deliberate
script) must type out, not a single `--yes`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

from app import config
from app.db import SignalStore
from app.routing import load_routing_config_from_store
from app.writer_lease import (
    LeaseStillValidError,
    WriterLeaseGuard,
    default_site_id,
)

logger = logging.getLogger(__name__)


def _print_lease_status(store: SignalStore) -> None:
    lease = store.get_writer_lease()
    if lease is None:
        print("writer_lease: none -- no site has ever acquired the writer role on this database")
        return
    now = datetime.now(timezone.utc)
    expired = lease.is_expired(now=now)
    print(
        f"writer_lease: site={lease.site_id!r} holder={lease.holder_id!r} token={lease.fencing_token} "
        f"acquired_at={lease.acquired_at.isoformat()} expires_at={lease.expires_at.isoformat()} "
        f"({'EXPIRED' if expired else 'still valid'} as of {now.isoformat()})"
    )


def _print_unresolved_command_ledger(store: SignalStore) -> None:
    """Surfaces P0-2's `command_ledger` unresolved entries as part of the
    promotion confirmation step, per this work's own instructions --
    P0-2 introduces a durable record of commands a writer submitted
    whose outcome wasn't yet confirmed at the time it stopped being
    writer (see that work's own module for the exact schema/columns once
    it lands). Only checked here on a best-effort basis: this repo's
    `writer_lease` table has no dependency on that table existing, so a
    database that predates it (or a checkout where that work hasn't been
    merged yet) prints a clear note instead of failing.

    Integration follow-up: once `command_ledger` (or whatever its final
    name is) exists, this should read and print every row whose outcome
    is still unresolved, not just report on its absence -- track that as
    a small follow-up patch here."""
    try:
        # No `SignalStore` accessor exists for this table (it lives in a
        # sibling patch) -- query it directly by name; missing table is
        # the expected case today.
        with store._connect() as conn:  # noqa: SLF001 - read-only diagnostic query, not a schema this store owns
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='command_ledger'"
            ).fetchone()
            if not exists:
                print(
                    "command_ledger: table not present in this database -- P0-2's command ledger isn't "
                    "merged/migrated here yet. This is a known integration follow-up (see this function's "
                    "own docstring), not a blocker recorded by this tool; the operator must still manually "
                    "reconcile any commands the prior writer may have submitted, per deploy/RUNBOOK.md step 2, "
                    "before confirming --confirm-reconciled."
                )
                return
            rows = conn.execute(
                "SELECT COUNT(*) FROM command_ledger WHERE outcome IS NULL OR outcome = 'unresolved'"
            ).fetchone()
        unresolved_count = rows[0] if rows else 0
        if unresolved_count:
            print(f"command_ledger: {unresolved_count} UNRESOLVED command(s) -- review these before confirming reconciliation")
        else:
            print("command_ledger: 0 unresolved commands")
    except Exception as exc:  # noqa: BLE001 - a diagnostic query failing must not crash promotion status reporting
        print(f"command_ledger: could not query ({exc!r}) -- treat as unknown, not as 'clean'")


def cmd_status(args: argparse.Namespace) -> int:
    store = SignalStore(config.DATABASE_PATH)
    _print_lease_status(store)
    _print_unresolved_command_ledger(store)
    return 0


async def _verify_account_identity(store: SignalStore) -> tuple[bool, list[str]]:
    """Re-verifies brokerage/account identity for every configured
    destination account before the new writer begins submitting
    commands, per this work's own instructions. Reuses app.main's own
    broker registry construction (the same credentials/wiring the real
    app would use) rather than re-implementing it here.

    Returns (all_verified_or_unverifiable, messages). A broker with no
    real balance-readback capability can't be automatically verified
    here at all -- that's reported as a message, not a failure, since
    plenty of real adapters (see app/brokers/*.py's own capability
    docstrings) have no verified way to do this; the operator is
    responsible for confirming those manually."""
    # Imported lazily, and only here: app.main constructs the FULL app
    # (every broker adapter, every source) at import time, which is
    # unnecessary for `status` and would make every invocation of this
    # CLI pay that cost and require every optional broker's env vars to
    # be importable even just to check lease status.
    from app.main import brokers as live_brokers  # noqa: PLC0415

    routing = load_routing_config_from_store(store)
    all_ok = True
    messages: list[str] = []
    for account in routing.accounts.values():
        broker = live_brokers.get(account.broker)
        if broker is None:
            all_ok = False
            messages.append(f"account={account.account_id}: no broker adapter registered for '{account.broker}'")
            continue
        if not broker.has_balance_capability:
            messages.append(
                f"account={account.account_id} broker={account.broker}: no automated balance/identity "
                "readback -- confirm this account manually before trusting this site as writer for it"
            )
            continue
        try:
            balance = await broker.get_account_balance(account)
        except Exception as exc:  # noqa: BLE001 - a broker call failing must count as unverified, not crash the check
            all_ok = False
            messages.append(f"account={account.account_id} broker={account.broker}: identity check FAILED ({exc!r})")
            continue
        if balance is None:
            all_ok = False
            messages.append(
                f"account={account.account_id} broker={account.broker}: identity check returned no balance "
                "-- broker/account not reachable or not confirmed"
            )
            continue
        messages.append(f"account={account.account_id} broker={account.broker}: identity check OK ({balance})")
    return all_ok, messages


def cmd_promote(args: argparse.Namespace) -> int:
    if not (args.confirm_old_writer_fenced and args.confirm_reconciled and args.confirm_identity):
        print(
            "REFUSED: promotion requires all three --confirm-old-writer-fenced, --confirm-reconciled, "
            "and --confirm-identity flags, each a deliberate acknowledgment that YOU have independently "
            "completed the corresponding step in deploy/RUNBOOK.md's 'Before promotion' checklist. This "
            "tool's own lease-expiry check (below) is an additional automatic guard, not a substitute for "
            "any of them.",
            file=sys.stderr,
        )
        return 2

    store = SignalStore(config.DATABASE_PATH)
    print("--- current lease ---")
    _print_lease_status(store)
    print("--- unresolved commands (--confirm-reconciled covers these) ---")
    _print_unresolved_command_ledger(store)

    if not args.skip_identity_check:
        print("--- re-verifying brokerage/account identity (--confirm-identity covers this) ---")
        all_ok, messages = asyncio.run(_verify_account_identity(store))
        for message in messages:
            print(message)
        if not all_ok:
            print(
                "REFUSED: at least one configured account's identity could not be verified. Fix "
                "connectivity/credentials and retry, or pass --skip-identity-check to proceed anyway "
                "(NOT recommended -- only for an account this site deliberately does not manage).",
                file=sys.stderr,
            )
            return 3
    else:
        print("--- identity check SKIPPED (--skip-identity-check) -- not recommended ---", file=sys.stderr)

    site_id = args.site_id or default_site_id()
    guard = WriterLeaseGuard(store, site_id=site_id, lease_seconds=config.WRITER_LEASE_SECONDS)
    try:
        record = store.promote_writer_lease(guard.site_id, guard.holder_id, guard.lease_seconds)
    except LeaseStillValidError as exc:
        print(
            f"REFUSED: {exc}\n"
            "This means the current lease does not look expired yet. Per deploy/RUNBOOK.md, confirm "
            "(independently, at the infrastructure/broker level -- never by trusting this) that the prior "
            "writer's host is genuinely stopped before proceeding; this tool will not promote over a lease "
            "that still looks valid.",
            file=sys.stderr,
        )
        return 4

    print(
        f"PROMOTED: site={record.site_id!r} holder={record.holder_id!r} is now fencing_token={record.fencing_token}. "
        f"Every process holding an older token is now fenced out on its next command-execution attempt. "
        "Continue with deploy/RUNBOOK.md's remaining 'Promotion steps' (unset STANDBY_MODE, start the "
        "service, watch GET /health, revoke sessions, reconnect sources, update DNS) before telling the "
        "owner this site is live."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.promote_cli",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status_parser = sub.add_parser("status", help="Show the current writer lease and unresolved-command status.")
    status_parser.set_defaults(func=cmd_status)

    promote_parser = sub.add_parser(
        "promote",
        help="Deliberately take over the writer role for this site. Requires all three --confirm-* flags.",
    )
    promote_parser.add_argument(
        "--site-id",
        default="",
        help="This site's identity (WRITER_SITE_ID). Defaults to the hostname if unset.",
    )
    promote_parser.add_argument(
        "--confirm-old-writer-fenced",
        action="store_true",
        help=(
            "I have independently confirmed the prior writer's host is stopped or its brokerage "
            "credentials revoked (deploy/RUNBOOK.md step 1) -- not merely that its lease looks expired."
        ),
    )
    promote_parser.add_argument(
        "--confirm-reconciled",
        action="store_true",
        help=(
            "I have reconciled any commands the prior writer may have submitted before dying "
            "(deploy/RUNBOOK.md step 2), including reviewing the unresolved command_ledger entries "
            "this command prints, if present."
        ),
    )
    promote_parser.add_argument(
        "--confirm-identity",
        action="store_true",
        help="I want this command to re-verify brokerage/account identity before promoting (recommended).",
    )
    promote_parser.add_argument(
        "--skip-identity-check",
        action="store_true",
        help="Skip the automated identity re-verification even with --confirm-identity set. NOT recommended.",
    )
    promote_parser.set_defaults(func=cmd_promote)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
