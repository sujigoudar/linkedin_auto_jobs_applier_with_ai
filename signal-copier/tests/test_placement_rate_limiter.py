"""P0 (batch C): app/placement_rate_limiter.py's persistent, DB-backed
admission control on OUTBOUND order-placement frequency -- wired into
app/engine.py's real order-placement path (both the plain-account and
managed_lifecycle entry paths), independent of app/rate_limit.py's C06
ingress-only HTTP abuse control.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.placement_rate_limiter import PlacementLimits, PlacementRateLimiter
from app.routing import RoutingConfig, RoutingRule

SOURCE = "tradingview"


def _engine(
    store: SignalStore,
    account: DestinationAccount,
    broker: PaperBroker,
    *,
    limits: PlacementLimits | None = None,
) -> SignalCopierEngine:
    routing = RoutingConfig(
        rules=[RoutingRule(source=SOURCE, destinations=[account.account_id])], accounts={account.account_id: account}
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    limiter = PlacementRateLimiter(store, limits) if limits is not None else None
    return SignalCopierEngine(
        routing=routing,
        brokers={"paper": broker},
        store=store,
        lifecycle_manager=lifecycle_manager,
        placement_rate_limiter=limiter,
    )


def _buy(symbol: str, analyst: str | None = None) -> Signal:
    return Signal(source=SOURCE, symbol=symbol, side=Side.BUY, quantity=1.0, price=10.0, analyst=analyst)


@pytest.mark.asyncio
async def test_no_limits_configured_never_blocks(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine(store, account, broker)  # default PlacementRateLimiter, every limit unset

    for i in range(10):
        results = await engine.handle_signal(_buy(f"SYM{i}"))
        assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_max_trades_per_hour_account_blocks_after_the_limit(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    limits = PlacementLimits(max_trades_per_hour_account=2)
    engine = _engine(store, account, broker, limits=limits)

    first = await engine.handle_signal(_buy("AAPL"))
    second = await engine.handle_signal(_buy("MSFT"))
    assert first[0].status == OrderStatus.FILLED
    assert second[0].status == OrderStatus.FILLED

    third = await engine.handle_signal(_buy("GOOG"))
    assert third[0].status == OrderStatus.REJECTED
    assert "placement rate limit exceeded" in third[0].message
    assert "account" in third[0].message
    assert broker.positions["acct1"].get("GOOG", 0.0) == 0.0  # never reached the broker


@pytest.mark.asyncio
async def test_max_trades_per_day_global_blocks_across_different_accounts(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account_a = DestinationAccount(account_id="acctA", broker="paper")
    account_b = DestinationAccount(account_id="acctB", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source=SOURCE, destinations=["acctA", "acctB"])],
        accounts={"acctA": account_a, "acctB": account_b},
    )
    limiter = PlacementRateLimiter(store, PlacementLimits(max_trades_per_day_global=3))
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, placement_rate_limiter=limiter
    )

    # Each signal fans out to BOTH accounts -> 2 order placements per signal.
    first = await engine.handle_signal(_buy("AAPL"))
    assert [r.status for r in first] == [OrderStatus.FILLED, OrderStatus.FILLED]

    # Global count is now 2; the 3rd placement (first of this pair) is still
    # admitted, the 4th (second of this pair) is rejected.
    second = await engine.handle_signal(_buy("MSFT"))
    statuses = [r.status for r in second]
    assert statuses.count(OrderStatus.FILLED) == 1
    assert statuses.count(OrderStatus.REJECTED) == 1


@pytest.mark.asyncio
async def test_max_trades_per_hour_provider_scoped_across_accounts(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    limits = PlacementLimits(max_trades_per_hour_provider=1)
    engine = _engine(store, account, broker, limits=limits)

    first = await engine.handle_signal(_buy("AAPL"))
    assert first[0].status == OrderStatus.FILLED

    second = await engine.handle_signal(_buy("MSFT"))
    assert second[0].status == OrderStatus.REJECTED
    assert "provider" in second[0].message


@pytest.mark.asyncio
async def test_max_trades_per_hour_analyst_scoped(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    limits = PlacementLimits(max_trades_per_hour_analyst=1)
    engine = _engine(store, account, broker, limits=limits)

    first = await engine.handle_signal(_buy("AAPL", analyst="trader_x"))
    assert first[0].status == OrderStatus.FILLED

    # A different analyst is a distinct scope, unaffected.
    other_analyst = await engine.handle_signal(_buy("MSFT", analyst="trader_y"))
    assert other_analyst[0].status == OrderStatus.FILLED

    second_same_analyst = await engine.handle_signal(_buy("GOOG", analyst="trader_x"))
    assert second_same_analyst[0].status == OrderStatus.REJECTED
    assert "analyst" in second_same_analyst[0].message

    # No analyst on the signal at all -- the analyst scope is skipped
    # entirely (nothing to check against), not silently blocked.
    no_analyst = await engine.handle_signal(_buy("TSLA", analyst=None))
    assert no_analyst[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_max_trades_per_hour_symbol_scoped(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    limits = PlacementLimits(max_trades_per_hour_symbol=1)
    engine = _engine(store, account, broker, limits=limits)

    first = await engine.handle_signal(_buy("AAPL"))
    assert first[0].status == OrderStatus.FILLED

    other_symbol = await engine.handle_signal(_buy("MSFT"))
    assert other_symbol[0].status == OrderStatus.FILLED

    second_same_symbol = await engine.handle_signal(_buy("AAPL"))
    assert second_same_symbol[0].status == OrderStatus.REJECTED
    assert "symbol" in second_same_symbol[0].message


@pytest.mark.asyncio
async def test_max_open_positions_account_blocks_a_new_distinct_symbol_but_not_adding_to_an_existing_one(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    limits = PlacementLimits(max_open_positions_account=1)
    engine = _engine(store, account, broker, limits=limits)

    first = await engine.handle_signal(_buy("AAPL"))
    assert first[0].status == OrderStatus.FILLED  # opens the account's one allowed position

    # Adding MORE to the SAME symbol doesn't increase the distinct-position
    # count -- not blocked by this dimension.
    add_to_existing = await engine.handle_signal(_buy("AAPL"))
    assert add_to_existing[0].status == OrderStatus.FILLED

    # A genuinely NEW distinct symbol would be a second simultaneous
    # position -- blocked.
    new_symbol = await engine.handle_signal(_buy("MSFT"))
    assert new_symbol[0].status == OrderStatus.REJECTED
    assert "simultaneous open positions" in new_symbol[0].message


@pytest.mark.asyncio
async def test_max_open_positions_global_counts_across_every_account(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account_a = DestinationAccount(account_id="acctA", broker="paper")
    account_b = DestinationAccount(account_id="acctB", broker="paper")
    routing = RoutingConfig(
        rules=[
            RoutingRule(source=SOURCE, destinations=["acctA"], symbol_filter=["AAPL"]),
            RoutingRule(source=SOURCE, destinations=["acctB"], symbol_filter=["MSFT"]),
        ],
        accounts={"acctA": account_a, "acctB": account_b},
    )
    limiter = PlacementRateLimiter(store, PlacementLimits(max_open_positions_global=1))
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, placement_rate_limiter=limiter
    )

    first = await engine.handle_signal(_buy("AAPL"))
    assert first[0].status == OrderStatus.FILLED  # one open position globally now

    second = await engine.handle_signal(_buy("MSFT"))
    assert second[0].status == OrderStatus.REJECTED
    assert "global" in second[0].message


@pytest.mark.asyncio
async def test_max_same_symbol_positions_account_blocks_pyramiding_when_set_to_one(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    limits = PlacementLimits(max_same_symbol_positions_account=1)
    engine = _engine(store, account, broker, limits=limits)

    first = await engine.handle_signal(_buy("AAPL"))
    assert first[0].status == OrderStatus.FILLED

    second = await engine.handle_signal(_buy("AAPL"))
    assert second[0].status == OrderStatus.REJECTED
    assert "simultaneous position" in second[0].message

    # A different, unrelated symbol is a separate scope, unaffected.
    different_symbol = await engine.handle_signal(_buy("MSFT"))
    assert different_symbol[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_managed_lifecycle_entry_is_also_gated_by_the_rate_limiter(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    limits = PlacementLimits(max_trades_per_hour_account=1)
    engine = _engine(store, account, broker, limits=limits)

    first = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=1.0, price=10.0, stop_loss=9.0)
    first_results = await engine.handle_signal(first)
    assert first_results[0].status == OrderStatus.FILLED

    second = Signal(source=SOURCE, symbol="MSFT", side=Side.BUY, quantity=1.0, price=10.0, stop_loss=9.0)
    second_results = await engine.handle_signal(second)
    assert second_results[0].status == OrderStatus.REJECTED
    assert "placement rate limit exceeded" in second_results[0].message
    # A rejected admission must not leave a stray lifecycle plan registered.
    assert engine.lifecycle_manager.get_lifecycle("acct1", "MSFT") is None


def test_limits_are_genuinely_persistent_across_a_simulated_restart(tmp_path):
    """Real persistence, not an in-memory counter: place two real entry
    orders through one SignalStore/PlacementRateLimiter, then open a BRAND
    NEW SignalStore against the same on-disk database file (simulating a
    process restart) and a brand new PlacementRateLimiter over it -- the
    limiter must still see both prior placements and block a third,
    proving the count comes from real persisted rows, not memory that a
    restart would have reset."""
    db_path = tmp_path / "test.db"
    store1 = SignalStore(db_path)
    for symbol in ("AAPL", "MSFT"):
        signal = Signal(source=SOURCE, symbol=symbol, side=Side.BUY, quantity=1.0, price=10.0)
        store1.save_signal(signal)
        from app.models import OrderResult

        store1.save_order_result(
            OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=signal.id, filled_quantity=1.0, filled_price=10.0),
            broker="paper",
            symbol=symbol,
            side=Side.BUY,
            requested_quantity=1.0,
            applied_quantity=1.0,
        )

    # Simulated restart: a fresh SignalStore + fresh PlacementRateLimiter
    # instance, over the SAME database file, no shared Python object at all.
    store2 = SignalStore(db_path)
    limiter2 = PlacementRateLimiter(store2, PlacementLimits(max_trades_per_hour_account=2))

    rejection = limiter2.check_admission(account_id="acct1", provider=SOURCE, analyst=None, symbol="GOOG")
    assert rejection is not None
    assert "placement rate limit exceeded" in rejection


def test_hourly_window_excludes_trades_older_than_an_hour(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    from app.models import OrderResult

    old_signal = Signal(source=SOURCE, symbol="AAPL", side=Side.BUY, quantity=1.0, price=10.0)
    store.save_signal(old_signal)
    store.save_order_result(
        OrderResult(
            account_id="acct1",
            status=OrderStatus.FILLED,
            signal_id=old_signal.id,
            filled_quantity=1.0,
            filled_price=10.0,
            executed_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ),
        broker="paper",
        symbol="AAPL",
        side=Side.BUY,
        requested_quantity=1.0,
        applied_quantity=1.0,
    )

    limiter = PlacementRateLimiter(store, PlacementLimits(max_trades_per_hour_account=1))
    # The only recorded trade is 2 hours old -- outside the 1-hour window,
    # so this new entry is still admitted.
    rejection = limiter.check_admission(account_id="acct1", provider=SOURCE, analyst=None, symbol="MSFT")
    assert rejection is None
