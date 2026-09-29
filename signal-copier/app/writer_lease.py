"""Cross-process, cross-host single-writer fencing.

## What this is (and isn't)

`deploy/RUNBOOK.md` already establishes the safety-first failover model
this repo actually runs: promotion is a deliberate, human-executed
procedure, and a database lease alone is explicitly called out there as
NOT proof that a prior writer's process has stopped (only a confirmed
cloud-provider stop/terminate, or a confirmed brokerage credential
revocation, is). This module does not change that -- see
`docs/FAILOVER.md`.

What it adds is a second, independent, *automatic* guard that sits below
the manual procedure: this deployment's only shared coordination point
across hosts is the SQLite database file itself (replicated between sites
via Litestream, per `deploy/RUNBOOK.md`). Postgres-style advisory locks
don't exist for SQLite, and even if they did, a lock alone has the exact
same "did the holder actually stop" gap the RUNBOOK already calls out.
What a shared database CAN give us, reliably, even across hosts and even
if an operator skips a step: a single, monotonically increasing
**fencing token**, issued exactly once per takeover, that every
command-execution path re-checks against the CURRENT token immediately
before acting. The moment a new token is issued (by the explicit
promotion action in `app/promote_cli.py` -- never automatically), every
process still holding an older token is fenced out on its very next
command attempt, regardless of whether its own lease row would otherwise
still look unexpired to it.

This makes the "old writer never actually stopped" gap fail SAFE instead
of fail silent: the manual host-stop / credential-revocation confirmation
in `deploy/RUNBOOK.md` remains the real proof the old writer can't act at
the broker; this fencing token means that even if that step were somehow
skipped or wrong, a stale writer's own commands are refused by this
process's own guard the instant a new token exists, not just "eventually,
once its lease looks expired to itself."

## What this deliberately does NOT do

No code path here ever calls `promote_writer_lease` automatically. A
standby process (`STANDBY_MODE=true`) never acquires, renews, or attempts
to promote a lease at all -- it is already refused write access by
`app/main.py`'s `_standby_read_only_gate` and never starts ingestion,
independent of this module entirely. An ACTIVE process whose own site
restarts (same `WRITER_SITE_ID`, e.g. systemd restarting the one
configured active service) reacquires automatically -- that is ordinary
operations, not a failover, and still bumps the fencing token so any
zombie instance of itself from before the restart is fenced too. Any
DIFFERENT site attempting to acquire, whether or not the existing lease
looks expired, is always refused automatic acquisition -- see
`acquire_or_reacquire_writer_lease`'s docstring. The only way a different
site ever becomes the writer is the explicit, human-run
`python -m app.promote_cli` command.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger(__name__)

#: How long an acquired/renewed lease is valid for before it would be
#: considered genuinely expired by `promote_writer_lease` -- see
#: `WriterLeaseGuard`'s renewal loop (app/main.py), which renews well
#: before this elapses under normal operation.
DEFAULT_LEASE_SECONDS = 30.0


class FencedOutError(RuntimeError):
    """Raised by `WriterLeaseGuard.require_active()`/`acquire_or_renew()`
    when this process's fencing token is no longer the current one in the
    `writer_lease` table (or no token was ever acquired). Every
    command-execution path that calls `require_active()` must let this
    propagate and refuse to submit the command -- never caught and
    ignored."""


class LeaseStillValidError(RuntimeError):
    """Raised by `SignalStore.promote_writer_lease` when the existing
    lease has not genuinely expired yet -- promotion while a lease might
    still be held by a live writer is refused. Per `docs/FAILOVER.md`,
    seeing this means: stop, and go confirm the prior writer's host is
    actually down (or its brokerage credentials revoked) before retrying
    -- do not treat this as a bug to route around."""


class WriterLeaseHeldByAnotherSiteError(RuntimeError):
    """Raised by `SignalStore.acquire_or_reacquire_writer_lease` when a
    DIFFERENT `site_id` already holds the lease record, whether or not it
    looks expired. This is the actual enforcement of "no automatic
    failover": only `promote_writer_lease` (the explicit, human-run
    action in `app/promote_cli.py`) may ever move the writer lease to a
    different site."""


@dataclass(frozen=True)
class WriterLeaseRecord:
    fencing_token: int
    site_id: str
    holder_id: str
    acquired_at: datetime
    expires_at: datetime
    renewed_at: datetime

    def is_expired(self, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return self.expires_at <= now


def default_site_id() -> str:
    """Falls back to the hostname when `WRITER_SITE_ID` isn't set (see
    `app/config.py`). A real active/passive deployment should set this
    explicitly and distinctly per site (e.g. "primary"/"standby-a") --
    two hosts that happen to share a hostname (containers, generic cloud
    images) would otherwise look like "the same site" to
    `acquire_or_reacquire_writer_lease` and be allowed to silently swap
    the writer role between them, defeating the whole point."""
    return socket.gethostname()


def _new_holder_id(site_id: str) -> str:
    return f"{site_id}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class _LeaseStore(Protocol):
    def get_writer_lease(self) -> WriterLeaseRecord | None: ...

    def acquire_or_reacquire_writer_lease(self, site_id: str, holder_id: str, lease_seconds: float) -> WriterLeaseRecord: ...

    def renew_writer_lease(self, holder_id: str, fencing_token: int, lease_seconds: float) -> bool: ...


class WriterLeaseGuard:
    """Held by exactly one running process's engine + lifecycle manager
    (the ACTIVE writer). Wraps a `SignalStore` (the only real
    cross-process/cross-host coordination point available here -- see
    this module's docstring) plus this process's own in-memory fencing
    token. `require_active()` is the actual per-command-execution check,
    and fails closed: no token yet, a DB error, or a token mismatch all
    raise `FencedOutError` rather than letting the command through."""

    def __init__(
        self,
        store: _LeaseStore,
        *,
        site_id: str | None = None,
        lease_seconds: float = DEFAULT_LEASE_SECONDS,
    ):
        self.store = store
        self.site_id = site_id or default_site_id()
        self.holder_id = _new_holder_id(self.site_id)
        self.lease_seconds = lease_seconds
        self._token: int | None = None
        # Guards the check-then-act read of `_token` plus the real
        # acquisition/renewal call and subsequent write of `_token` below.
        # `acquire()`/`renew()` can be entered concurrently from DIFFERENT
        # OS THREADS against this SAME guard instance -- not just
        # different coroutines on one event loop -- because FastAPI's
        # `lifespan()` runs on anyio's thread-based portal, and this
        # codebase's test suite opens many overlapping `TestClient`
        # context managers, each re-entering `lifespan()` (and thus
        # `acquire()`) on its own worker thread against the one
        # process-wide `app.main` singleton guard. Without this lock, two
        # threads can both observe `_token is None` (or both observe the
        # same stale `_token`), both perform a REAL acquisition/renewal
        # against the store (bumping the DB's fencing token twice), and
        # then race to write `self._token` -- whichever write lands last
        # wins, and it can be the STALE lower value, leaving this process
        # fenced out by its own second acquisition (FencedOutError on the
        # very next `require_active()`), not by any genuine second writer.
        # A plain `threading.Lock` (not `asyncio.Lock`, which only
        # excludes coroutines on one loop, not OS threads) closes this.
        self._lock = threading.Lock()

    @property
    def fencing_token(self) -> int | None:
        return self._token

    def acquire(self) -> int:
        """Called at startup, before this process starts accepting any
        signal/close/etc. that could reach a broker. Idempotent PER GUARD
        INSTANCE/OS PROCESS: a genuinely new process (a real restart)
        always has a brand-new `WriterLeaseGuard` object with `_token is
        None`, so its first `acquire()` always performs the real
        database acquisition below -- correctly bumping the token and
        superseding any previous instance of this same site, per
        `docs/FAILOVER.md`. A REPEATED call on a guard that has already
        acquired (this process's own `app/main.py` `lifespan` running
        again within the SAME already-live process -- which never
        happens in real uvicorn/gunicorn deployment, but does happen
        across this codebase's own test suite, which re-enters
        `TestClient`/`lifespan` many times against the one process-wide
        `app.main` singleton) does NOT bump the token again -- it would
        otherwise fence out THIS SAME PROCESS's own in-flight work every
        time, which is not a real failover, just repeated test scaffolding
        exercising the same already-running process. Instead it just
        re-verifies this process's existing token is still current
        (`require_active()`), and returns it unchanged.

        Raises `WriterLeaseHeldByAnotherSiteError` on the real (first)
        acquisition if a different site currently holds the lease (see
        that error's docstring) -- this process must not start as an
        active writer in that case; use `app/promote_cli.py` instead,
        deliberately. Raises `FencedOutError` on a repeated call if this
        process's own previously-acquired token has since been
        superseded (e.g. an explicit promotion actually happened) --
        never silently re-acquires past that."""
        if self._token is not None:
            self.require_active()
            return self._token
        with self._lock:
            # Re-check inside the lock: another thread may have completed
            # the real acquisition while this thread was waiting on
            # `_lock`. If so, this is just the idempotent repeated-call
            # path -- verify and return, never acquire a second time.
            if self._token is not None:
                self.require_active()
                return self._token
            record = self.store.acquire_or_reacquire_writer_lease(self.site_id, self.holder_id, self.lease_seconds)
            self._token = record.fencing_token
            logger.info(
                "writer lease acquired: site=%s holder=%s token=%s", self.site_id, self.holder_id, self._token
            )
            return self._token

    def renew(self) -> int:
        """Heartbeat: extends this process's own lease row. If this
        process no longer holds the CURRENT token (a new token was
        issued -- i.e. this process has been promoted-over, or its own
        row was otherwise superseded), this raises `FencedOutError`
        instead of silently re-acquiring -- a fenced process must never
        re-arm itself as writer; that would BE the automatic-failover
        hole this whole module exists to close."""
        with self._lock:
            if self._token is None:
                raise FencedOutError(f"site={self.site_id} holder={self.holder_id} has no lease to renew")
            ok = self.store.renew_writer_lease(self.holder_id, self._token, self.lease_seconds)
            if not ok:
                raise FencedOutError(
                    f"site={self.site_id} holder={self.holder_id} token={self._token} is no longer the "
                    "current writer lease -- fenced out"
                )
            return self._token

    def require_active(self) -> None:
        """The real per-command-execution fencing check. Call this
        immediately before any broker-write call (`place_order`,
        `cancel_order`, `replace_stop_quantity`, ...). Fails closed on
        every ambiguous outcome: no acquired token, a lease row that's
        gone, or a lease row whose token no longer matches this
        process's -- all raise `FencedOutError`. Deliberately does NOT
        check `expires_at` here: a token mismatch fences a process out
        the instant a new token is issued, even if this process's own
        (now-superseded) lease row wouldn't otherwise look expired to it
        yet -- see this module's docstring."""
        if self._token is None:
            raise FencedOutError(f"site={self.site_id} holder={self.holder_id} never acquired a writer lease")
        try:
            current = self.store.get_writer_lease()
        except Exception as exc:  # noqa: BLE001 - fail closed: a DB error must never be treated as "still valid"
            raise FencedOutError(
                f"could not verify writer lease for site={self.site_id} holder={self.holder_id}: {exc!r}"
            ) from exc
        if current is None or current.fencing_token != self._token:
            raise FencedOutError(
                f"site={self.site_id} holder={self.holder_id} token={self._token} superseded by "
                f"{current.fencing_token if current else 'none'} -- fenced out, refusing to execute"
            )


class NullLeaseGuard:
    """No-op guard: the default for `SignalCopierEngine`/
    `PositionLifecycleManager` construction so every existing test/
    embedding that doesn't wire real fencing (a unit test's own
    single-process SQLite file, an ad-hoc script) is unaffected.
    `app/main.py` is the ONE place that must construct a real
    `WriterLeaseGuard` instead, for the app's own live ACTIVE process --
    never for `STANDBY_MODE`, which is already blocked from writing by
    `_standby_read_only_gate`/`lifespan` independent of this class
    entirely."""

    site_id = "unfenced"
    holder_id = "unfenced"

    def require_active(self) -> None:
        return None

    def acquire(self) -> int | None:
        return None

    def renew(self) -> int | None:
        return None

    @property
    def fencing_token(self):
        return None
