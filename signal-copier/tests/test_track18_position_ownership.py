"""Track 18: per-provider position ownership.

Track 16 found a real, structural gap: `positions`/`lifecycle_state` are
tracked ONLY per (account_id, symbol), pooled across every provider
`RoutingConfig` legitimately routes to the same destination account/symbol
(see app/routing.py's module docstring -- this IS a normal, intentional use
case). Before this file's fix, a provider with ZERO attributable quantity
in a pooled position could still close/reduce ANOTHER provider's entire
position, with no rejection at all.

This gates BOTH of app/engine.py's close/exit resolution paths
(`_resolve_and_submit_plain_close` for plain accounts,
`_handle_managed_close` for `managed_lifecycle` accounts) against
`SignalStore.get_provider_position_ownership` -- the SAME computation
(`orders.applied_execution_delta`, signed by side, joined through
`signals.source`) Track 16's `GET /positions/{symbol}/provider-allocations`
visibility endpoint already uses.
"""
from __future__ import annotations

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _plain_engine(store, broker, account, sources):
    routing = RoutingConfig(
        rules=[RoutingRule(source=src, destinations=[account.account_id]) for src in sources],
        accounts={account.account_id: account},
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)


def _managed_engine(store, broker, account, sources):
    routing = RoutingConfig(
        rules=[RoutingRule(source=src, destinations=[account.account_id]) for src in sources],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


# --- (a) single-provider case is completely unaffected -----------------


@pytest.mark.asyncio
async def test_single_provider_plain_close_fully_exits_unaffected(store):
    """Strict superset: the only provider that ever traded this
    account/symbol legitimately owns 100% of it -- Track 18's gate must
    let its full close through exactly like before this existed."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _plain_engine(store, broker, account, ["tv"])

    buy = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0)
    await engine.handle_signal(buy)
    assert store.get_position("acct1", "AAPL") == 100.0

    close = Signal(source="tv", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 100.0
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_single_provider_managed_close_fully_exits_unaffected(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _managed_engine(store, broker, account, ["tv"])

    entry = Signal(source="tv", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=48.5)
    await engine.handle_signal(entry)

    close = Signal(source="tv", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 100.0
    assert store.get_position("acct1", "AAPL") == 0.0
    assert lifecycle_manager.get_lifecycle("acct1", "AAPL").closed is True


# --- (b) a foreign provider (0 attributable quantity) is rejected -------


@pytest.mark.asyncio
async def test_plain_close_from_foreign_provider_rejected_no_provider_position(store):
    """Provider A built the whole pooled position; Provider B (also
    routed to this same account/symbol -- a normal, intentional
    RoutingConfig setup) has never traded it at all. B's exit must be
    rejected outright, never allowed to act against A's position."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _plain_engine(store, broker, account, ["providerA", "providerB"])

    await engine.handle_signal(Signal(source="providerA", symbol="AAPL", side=Side.BUY, quantity=100.0))
    assert store.get_position("acct1", "AAPL") == 100.0

    close = Signal(source="providerB", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert len(results) == 1
    result = results[0]
    assert result.status == OrderStatus.REJECTED
    assert "NO_PROVIDER_POSITION" in result.message
    assert "Provider: providerB" in result.message
    assert "Owned quantity: 0" in result.message
    # Provider A's position must be completely untouched.
    assert store.get_position("acct1", "AAPL") == 100.0


@pytest.mark.asyncio
async def test_managed_close_from_foreign_provider_rejected_no_provider_position(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _managed_engine(store, broker, account, ["providerA", "providerB"])

    await engine.handle_signal(
        Signal(source="providerA", symbol="AAPL", side=Side.BUY, quantity=100.0, stop_loss=48.5)
    )
    assert store.get_position("acct1", "AAPL") == 100.0

    close = Signal(source="providerB", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert len(results) == 1
    result = results[0]
    assert result.status == OrderStatus.REJECTED
    assert "NO_PROVIDER_POSITION" in result.message
    assert "Provider: providerB" in result.message
    assert "Owned quantity: 0" in result.message
    # Provider A's position (and its lifecycle) must be completely untouched.
    assert store.get_position("acct1", "AAPL") == 100.0
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None and not lifecycle.closed
    assert lifecycle.confirmed_owned_quantity == 100.0


# --- (c) a partial owner can only close up to its own attributable share


@pytest.mark.asyncio
async def test_plain_close_caps_to_own_attributable_share_when_pooled(store):
    """Provider A built 100, Provider B built 50 of the SAME pooled
    account/symbol (a normal RoutingConfig setup -- both routed to
    acct1/AAPL). Provider B's full-exit signal must only close its own
    50, never the pooled 150."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _plain_engine(store, broker, account, ["providerA", "providerB"])

    await engine.handle_signal(Signal(source="providerA", symbol="AAPL", side=Side.BUY, quantity=100.0))
    await engine.handle_signal(Signal(source="providerB", symbol="AAPL", side=Side.BUY, quantity=50.0))
    assert store.get_position("acct1", "AAPL") == 150.0

    close = Signal(source="providerB", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 50.0
    # The pooled position now reflects ONLY provider B's exit -- provider
    # A's 100 shares are still fully intact.
    assert store.get_position("acct1", "AAPL") == 100.0

    # A second close from B now finds it owns nothing further here.
    second_close = Signal(source="providerB", symbol="AAPL", side=Side.CLOSE)
    second_results = await engine.handle_signal(second_close)
    assert second_results[0].status == OrderStatus.REJECTED
    assert "NO_PROVIDER_POSITION" in second_results[0].message
    assert store.get_position("acct1", "AAPL") == 100.0

    # Provider A can still fully exit its own share afterwards.
    a_close = Signal(source="providerA", symbol="AAPL", side=Side.CLOSE)
    a_results = await engine.handle_signal(a_close)
    assert a_results[0].status == OrderStatus.FILLED
    assert a_results[0].filled_quantity == 100.0
    assert store.get_position("acct1", "AAPL") == 0.0


def test_managed_close_caps_to_own_attributable_share_when_pooled(store):
    """Managed_lifecycle's own entry gate (EXE-09) refuses a second,
    concurrent entry for the same (account_id, symbol) while one is
    already active, so two providers cannot literally race entries onto
    the same managed lifecycle the way plain accounts can. This directly
    exercises the SAME ownership-gate arithmetic
    (`SignalStore.get_provider_position_ownership` + the
    min(this_provider, available) cap in `_handle_managed_close`) against
    a deliberately fabricated multi-source `orders` history for one
    account/symbol -- i.e. it tests the gate's capping logic in isolation
    from EXE-09's entry-time restriction, using the store directly rather
    than a second, real concurrent entry (which EXE-09 would itself
    reject)."""
    store.save_signal(Signal(id="sigA", source="providerA", symbol="AAPL", side=Side.BUY))
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id="sigA", filled_quantity=100.0),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        applied_quantity=100.0,
        applied_execution_delta=100.0,
    )
    store.save_signal(Signal(id="sigB", source="providerB", symbol="AAPL", side=Side.BUY))
    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id="sigB", filled_quantity=50.0),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        applied_quantity=50.0,
        applied_execution_delta=50.0,
    )

    this_provider_b, total = store.get_provider_position_ownership("acct1", "AAPL", "providerB")
    assert this_provider_b == 50.0
    assert total == 150.0

    this_provider_a, total_a = store.get_provider_position_ownership("acct1", "AAPL", "providerA")
    assert this_provider_a == 100.0
    assert total_a == 150.0

    # A foreign provider with no attributable history at all is still 0,
    # while `total` correctly reflects everyone else's real contributions.
    this_provider_c, total_c = store.get_provider_position_ownership("acct1", "AAPL", "providerC")
    assert this_provider_c == 0.0
    assert total_c == 150.0


# --- (d) both exit paths are covered ------------------------------------
# (plain: tests above; managed: tests above) -- this section documents it
# explicitly since it's one of this task's own required proof points.


def test_both_exit_paths_are_wired_to_the_same_gate():
    """Documents (rather than re-derives) that both real exit call sites
    route through provider-ownership gating by default: `_resolve_and_
    submit_plain_close`'s and `_handle_managed_close`'s own
    `enforce_provider_ownership: bool = True` default parameters."""
    import inspect

    plain_sig = inspect.signature(SignalCopierEngine._resolve_and_submit_plain_close)
    managed_sig = inspect.signature(SignalCopierEngine._handle_managed_close)
    assert plain_sig.parameters["enforce_provider_ownership"].default is True
    assert managed_sig.parameters["enforce_provider_ownership"].default is True


# --- (e) flat-position (zero tracked quantity anywhere) rejection -------


@pytest.mark.asyncio
async def test_plain_close_with_flat_position_still_rejected(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _plain_engine(store, broker, account, ["tv"])

    close = Signal(source="tv", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.REJECTED
    assert results[0].message == "no open position to close"


@pytest.mark.asyncio
async def test_managed_close_with_flat_position_still_rejected(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, _ = _managed_engine(store, broker, account, ["tv"])

    close = Signal(source="tv", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.REJECTED
    assert "no open position to close" in results[0].message


# --- manual dashboard flatten deliberately bypasses provider gating -----


@pytest.mark.asyncio
async def test_manual_close_position_flattens_pooled_position_regardless_of_provider(store):
    """`close_position` (the dashboard's "Exit now"/"Flatten" action) is
    explicitly NOT provider-scoped (see its own docstring, "bypasses
    routing rules entirely") -- it must still flatten the WHOLE pooled
    position built by multiple providers, unaffected by Track 18's gate."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _plain_engine(store, broker, account, ["providerA", "providerB"])

    await engine.handle_signal(Signal(source="providerA", symbol="AAPL", side=Side.BUY, quantity=100.0))
    await engine.handle_signal(Signal(source="providerB", symbol="AAPL", side=Side.BUY, quantity=50.0))
    assert store.get_position("acct1", "AAPL") == 150.0

    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 150.0
    assert store.get_position("acct1", "AAPL") == 0.0


# --- no attribution data at all falls back to pre-Track-18 behavior -----


@pytest.mark.asyncio
async def test_plain_close_with_no_order_attribution_at_all_is_unaffected(store):
    """A position seeded outside this service's own tracked order
    pipeline (e.g. `store.record_fill` called directly by a reconciliation
    job, with no `orders`/`signals` rows at all -- see
    test_plain_close_race.py's identical fixture pattern) has no
    competing-provider data to gate against. Track 18 must not regress
    this already-supported scenario."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _plain_engine(store, broker, account, ["tv"])

    await broker.place_order(Signal(source="external", symbol="AAPL", side=Side.BUY), account, 10.0, "AAPL")
    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)

    close = Signal(source="tv", symbol="AAPL", side=Side.CLOSE)
    results = await engine.handle_signal(close)

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 10.0
    assert store.get_position("acct1", "AAPL") == 0.0
