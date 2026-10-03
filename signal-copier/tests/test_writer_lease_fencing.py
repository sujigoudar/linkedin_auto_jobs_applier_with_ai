"""Cross-process/cross-host single-writer fencing (see app/writer_lease.py,
docs/FAILOVER.md).

Covers:

1. Two "writer" engine instances sharing the same database: the second
   being promoted to a new fencing token causes the first to correctly
   detect it's been fenced out (FencedOutError) on its next
   command-execution attempt, even though the first's own lease row
   wouldn't otherwise look expired to it yet.
2. A promotion attempt while the current lease is still valid/unexpired
   is rejected (LeaseStillValidError) -- no new token is issued.
3. A promotion attempt after genuine expiry succeeds and issues a new,
   strictly higher token.
4. A different site can never acquire the lease automatically (whether or
   not the existing lease looks expired) -- only the explicit promote
   path can move the writer role across sites.
5. Same-site reacquisition (an ordinary restart) succeeds automatically
   and still bumps the token, fencing any lingering same-site zombie.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule
from app.writer_lease import (
    FencedOutError,
    LeaseStillValidError,
    WriterLeaseGuard,
    WriterLeaseHeldByAnotherSiteError,
)


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


# --- SignalStore-level lease mechanics ---------------------------------


def test_first_acquire_claims_token_one(store):
    record = store.acquire_or_reacquire_writer_lease("site-a", "site-a:1:aaaa", 30.0)
    assert record.fencing_token == 1
    assert record.site_id == "site-a"


def test_same_site_reacquire_bumps_token_automatically(store):
    first = store.acquire_or_reacquire_writer_lease("site-a", "site-a:1:aaaa", 30.0)
    # Same site, new process instance (new holder_id) -- an ordinary
    # restart, not a failover. Must succeed automatically and bump the
    # token so any lingering instance of the previous process is fenced.
    second = store.acquire_or_reacquire_writer_lease("site-a", "site-a:2:bbbb", 30.0)
    assert second.fencing_token == first.fencing_token + 1
    assert second.holder_id == "site-a:2:bbbb"


def test_different_site_cannot_acquire_automatically_even_when_expired(store):
    now = datetime.now(timezone.utc)
    store.acquire_or_reacquire_writer_lease("site-a", "site-a:1:aaaa", 30.0, now=now - timedelta(seconds=1000))
    # site-a's lease is now long expired -- a DIFFERENT site must still be
    # refused automatic acquisition. This is the actual enforcement of
    # "no automatic failover, even after a heartbeat disappears."
    with pytest.raises(WriterLeaseHeldByAnotherSiteError):
        store.acquire_or_reacquire_writer_lease("site-b", "site-b:1:cccc", 30.0, now=now)


def test_promote_rejected_while_lease_still_valid(store):
    store.acquire_or_reacquire_writer_lease("site-a", "site-a:1:aaaa", 30.0)
    with pytest.raises(LeaseStillValidError):
        store.promote_writer_lease("site-b", "site-b:1:cccc", 30.0)
    # No new token issued -- the lease is untouched.
    lease = store.get_writer_lease()
    assert lease.site_id == "site-a"
    assert lease.fencing_token == 1


def test_promote_succeeds_after_genuine_expiry_and_issues_new_token(store):
    now = datetime.now(timezone.utc)
    store.acquire_or_reacquire_writer_lease("site-a", "site-a:1:aaaa", 30.0, now=now - timedelta(seconds=1000))
    record = store.promote_writer_lease("site-b", "site-b:1:cccc", 30.0, now=now)
    assert record.fencing_token == 2
    assert record.site_id == "site-b"
    lease = store.get_writer_lease()
    assert lease.fencing_token == 2
    assert lease.site_id == "site-b"


def test_promote_with_no_existing_lease_issues_token_one(store):
    record = store.promote_writer_lease("site-a", "site-a:1:aaaa", 30.0)
    assert record.fencing_token == 1


def test_renew_fails_for_superseded_holder(store):
    first = store.acquire_or_reacquire_writer_lease("site-a", "site-a:1:aaaa", 30.0)
    # A new token issued (promotion, or same-site reacquire) -- the old
    # holder's renewal must now fail, not silently "succeed" against a
    # row that's no longer theirs.
    store.acquire_or_reacquire_writer_lease("site-a", "site-a:2:bbbb", 30.0)
    assert store.renew_writer_lease(first.holder_id, first.fencing_token, 30.0) is False


def test_concurrent_acquire_from_multiple_threads_is_race_free(store, monkeypatch):
    """P0-6 hardening: `WriterLeaseGuard.acquire()` is called from
    FastAPI's `lifespan()`, which runs on anyio's thread-based portal --
    so a single process-wide guard singleton (`app/main.py`) CAN see
    `acquire()` entered from multiple real OS threads at once (this
    codebase's own test suite does exactly that: ~150+ files each opening
    a `TestClient` context manager, each re-entering `lifespan()` on its
    own worker thread against the one shared `app.main` singleton).

    Without a lock around the check-then-act `_token is None` /
    `store.acquire_or_reacquire_writer_lease` / `_token = ...` sequence,
    two threads can both see `_token is None`, both perform a REAL
    acquisition (each bumping the DB's fencing token), and then race to
    write `_token` -- whichever write lands last wins, and it can be the
    STALE lower value, leaving this process fenced out by its own second
    acquisition on the very next `require_active()` call. This asserts
    the actual invariant: exactly one real acquisition happens, and every
    thread ends up agreeing on the same final token -- which also must be
    the token genuinely current in the store."""
    guard = WriterLeaseGuard(store, site_id="site-a")

    call_count = 0
    call_count_lock = threading.Lock()
    real_acquire = store.acquire_or_reacquire_writer_lease

    def slow_acquire(*args, **kwargs):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
        # Widen the race window between the guard's "_token is None"
        # check and the store call actually landing, so the race
        # reproduces reliably rather than depending on incidental
        # scheduling luck.
        time.sleep(0.05)
        return real_acquire(*args, **kwargs)

    monkeypatch.setattr(store, "acquire_or_reacquire_writer_lease", slow_acquire)

    num_threads = 8
    barrier = threading.Barrier(num_threads)
    results: list[int | None] = [None] * num_threads
    errors: list[BaseException | None] = [None] * num_threads

    def worker(idx: int) -> None:
        barrier.wait()  # maximize simultaneity: all threads enter acquire() together
        try:
            results[idx] = guard.acquire()
        except BaseException as exc:  # noqa: BLE001 - captured and asserted on below
            errors[idx] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "a worker thread did not finish -- possible deadlock"

    assert all(e is None for e in errors), f"unexpected errors from concurrent acquire(): {errors}"
    assert call_count == 1, f"expected exactly one real store acquisition, got {call_count}"
    assert len(set(results)) == 1, f"threads disagreed on the final fencing token: {results}"
    assert guard.fencing_token == results[0]
    current = store.get_writer_lease()
    assert current is not None
    assert current.fencing_token == guard.fencing_token, (
        "guard's in-memory token diverged from the store's current token -- "
        "this is exactly the self-inflicted false-fencing bug this test guards against"
    )


# --- WriterLeaseGuard-level behavior -------------------------------------


def test_guard_require_active_fails_before_acquire(store):
    guard = WriterLeaseGuard(store, site_id="site-a")
    with pytest.raises(FencedOutError):
        guard.require_active()


def test_guard_require_active_passes_after_acquire(store):
    guard = WriterLeaseGuard(store, site_id="site-a")
    guard.acquire()
    guard.require_active()  # must not raise


@pytest.mark.scenario("OPS-005")
def test_second_promotion_fences_out_the_first_guard_immediately(store):
    """The core scenario: two "writer" instances against the same DB. The
    second acquiring a new (genuinely-issued) fencing token must cause
    the first to detect it's fenced out on its very next check -- even
    before its own lease row would otherwise look expired to it. This is
    the load-bearing property `require_active()` provides (deliberately
    NOT gated on `expires_at`)."""
    guard_a = WriterLeaseGuard(store, site_id="site-a", lease_seconds=30.0)
    guard_a.acquire()
    guard_a.require_active()  # fine: still the only writer

    # Simulate a genuine, deliberate promotion of a different site (what
    # `app/promote_cli.py` would do) -- force the existing lease to look
    # expired first, exactly like a real crash-then-promote scenario,
    # WITHOUT waiting 30 real seconds.
    now = datetime.now(timezone.utc)
    store.promote_writer_lease("site-b", "site-b:1:zzzz", 30.0, now=now + timedelta(hours=1))

    # site-a's guard still thinks its own token is current and its own
    # `expires_at` (30s from its original acquire()) hasn't even been
    # reached in wall-clock terms -- but it must be fenced out anyway,
    # because require_active() checks token identity, not expiry.
    with pytest.raises(FencedOutError):
        guard_a.require_active()


def test_renew_after_fencing_raises(store):
    guard_a = WriterLeaseGuard(store, site_id="site-a")
    guard_a.acquire()
    store.acquire_or_reacquire_writer_lease("site-a", "site-a:2:new-instance", 30.0)  # a "restart" supersedes it
    with pytest.raises(FencedOutError):
        guard_a.renew()


# --- Track 45: direct unit coverage closing survivors mutmut found once
# writer_lease.py was added to the widened mutation scope -- in particular
# `renew()`'s happy path was NEVER exercised by any existing test (only
# the fenced-out-raises case was), so a mutant that flipped `if self._token
# is None` to `is not None` (making renew() always raise, even for a
# genuinely still-current writer -- which would wrongly fence out a live
# writer's own heartbeat) survived undetected. ---


def test_guard_renew_succeeds_and_returns_the_current_token_while_still_active(store):
    """The real heartbeat path (app/main.py calls this on a timer): a
    guard that is still the genuine, current writer must be able to renew
    repeatedly without ever raising -- this was never asserted anywhere
    before Track 45."""
    guard = WriterLeaseGuard(store, site_id="site-a", lease_seconds=30.0)
    token = guard.acquire()
    renewed_token = guard.renew()
    assert renewed_token == token
    # A second renewal must also succeed -- not a one-shot fluke.
    assert guard.renew() == token
    guard.require_active()  # still genuinely current


def test_is_expired_true_exactly_at_the_boundary(store):
    """`is_expired` uses `<=`, not `<`: a lease whose `expires_at` is
    exactly `now` must be treated as expired (so `promote_writer_lease`
    can issue a new token right at the boundary instant, never leaving a
    gap where it's neither clearly valid nor clearly expired)."""
    from app.writer_lease import WriterLeaseRecord

    now = datetime.now(timezone.utc)
    record = WriterLeaseRecord(
        fencing_token=1, site_id="site-a", holder_id="h1", acquired_at=now, expires_at=now, renewed_at=now
    )
    assert record.is_expired(now=now) is True


def test_is_expired_direct_call_with_no_now_argument_uses_the_real_clock(store):
    """`is_expired()`'s own `now = now or datetime.now(timezone.utc)`
    fallback is never hit through either real call site (app/db.py and
    app/promote_cli.py both always pass `now=` explicitly) -- but the
    method itself must still behave correctly if called directly with no
    argument at all, not raise (e.g. a naive/aware comparison crash from
    a wrong timezone default)."""
    from app.writer_lease import WriterLeaseRecord

    far_future = datetime.now(timezone.utc) + timedelta(hours=1)
    not_yet_expired = WriterLeaseRecord(
        fencing_token=1, site_id="site-a", holder_id="h1",
        acquired_at=datetime.now(timezone.utc), expires_at=far_future, renewed_at=datetime.now(timezone.utc),
    )
    assert not_yet_expired.is_expired() is False

    far_past = datetime.now(timezone.utc) - timedelta(hours=1)
    already_expired = WriterLeaseRecord(
        fencing_token=1, site_id="site-a", holder_id="h1",
        acquired_at=far_past, expires_at=far_past, renewed_at=far_past,
    )
    assert already_expired.is_expired() is True


def test_holder_id_is_prefixed_with_its_own_site_id(store):
    """`holder_id` is built from THIS guard's own `site_id`
    (`_new_holder_id(self.site_id)`) -- a guard constructed for "site-a"
    must never end up with a holder_id built from a different (or missing)
    site identity; several debugging/ops paths parse this prefix."""
    guard = WriterLeaseGuard(store, site_id="site-a")
    assert guard.holder_id.startswith("site-a:")

    other = WriterLeaseGuard(store, site_id="site-b")
    assert other.holder_id.startswith("site-b:")
    assert other.holder_id != guard.holder_id


def test_new_holder_id_format_has_the_real_pid_and_an_8_char_hex_suffix():
    from app.writer_lease import _new_holder_id

    holder_id = _new_holder_id("site-a")
    parts = holder_id.split(":")
    assert len(parts) == 3
    assert parts[0] == "site-a"
    assert parts[1] == str(os.getpid())
    assert len(parts[2]) == 8
    int(parts[2], 16)  # a real hex string, not garbage


# --- End-to-end: two engine instances sharing one store -------------------


@pytest.fixture
def broker():
    return PaperBroker()


def _make_engine(store, broker, guard):
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store, lease_guard=guard)


@pytest.mark.asyncio
async def test_fenced_engine_refuses_to_execute_further_commands(store, broker):
    """The exact scenario the task requires: two "writer" engine
    instances against the same database. Engine A is the original
    writer; Engine B is promoted (a deliberate, explicit new token, as
    `app/promote_cli.py` would issue). Engine A must then refuse to
    execute ANY further signal -- not silently keep trading."""
    guard_a = WriterLeaseGuard(store, site_id="site-a", lease_seconds=30.0)
    guard_a.acquire()
    engine_a = _make_engine(store, broker, guard_a)

    # Engine A can trade while it's genuinely the current writer.
    buy_signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=1.0)
    results = await engine_a.handle_signal(buy_signal)
    assert results and results[0].status == OrderStatus.FILLED

    # Deliberate promotion of a different site (site-b) -- exactly what
    # a human running promote_cli after confirming site-a's host is down
    # would trigger. Force genuine expiry first, same as the real
    # promote_writer_lease precondition.
    now = datetime.now(timezone.utc)
    store.promote_writer_lease("site-b", "site-b:1:new", 30.0, now=now + timedelta(hours=1))
    guard_b = WriterLeaseGuard(store, site_id="site-b", lease_seconds=30.0)
    guard_b._token = 2  # what a real promote_cli-driven process would hold after its own acquire()
    engine_b = _make_engine(store, broker, guard_b)

    # Engine B (the new writer) can trade.
    sell_signal = Signal(source="tradingview", symbol="MSFT", side=Side.BUY, quantity=1.0)
    results_b = await engine_b.handle_signal(sell_signal)
    assert results_b and results_b[0].status == OrderStatus.FILLED

    # Engine A (the fenced-out former writer) must refuse -- not place
    # another order, not report a REJECTED OrderResult either: this is a
    # "this process must stop acting as writer" condition, distinct from
    # an ordinary per-signal rejection.
    another_signal = Signal(source="tradingview", symbol="GOOG", side=Side.BUY, quantity=1.0)
    with pytest.raises(FencedOutError):
        await engine_a.handle_signal(another_signal)

    # And close_position (the OTHER top-level entry point) is refused too.
    account = engine_a.routing.accounts["acct1"]
    with pytest.raises(FencedOutError):
        await engine_a.close_position(account, "AAPL")


@pytest.mark.asyncio
async def test_default_null_guard_never_fences_existing_tests(store, broker):
    """Every existing construction of SignalCopierEngine (no lease_guard
    passed) must be completely unaffected by this change."""
    engine = _make_engine(store, broker, None)
    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=1.0)
    results = await engine.handle_signal(signal)
    assert results and results[0].status == OrderStatus.FILLED
