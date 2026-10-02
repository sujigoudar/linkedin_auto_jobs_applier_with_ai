import pytest

from app.brokers.paper import PaperBroker
from app.models import DestinationAccount, OrderStatus, Side, Signal


@pytest.fixture
async def broker_with_long_position():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=62.0, symbol="AAPL"
    )
    return broker, account


@pytest.mark.asyncio
async def test_place_protective_stop_rests_pending(broker_with_long_position):
    broker, account = broker_with_long_position

    result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    assert result.status == OrderStatus.PENDING
    assert result.broker_order_id is not None
    # doesn't fill until the price actually trades through it
    assert broker.simulate_price("AAPL", 49.00) == []


@pytest.mark.asyncio
async def test_simulate_price_fills_stop_when_touched(broker_with_long_position):
    broker, account = broker_with_long_position
    stop_result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    fills = broker.simulate_price("AAPL", 48.40)

    assert len(fills) == 1
    assert fills[0].broker_order_id == stop_result.broker_order_id
    assert fills[0].filled_quantity == 62.0
    assert fills[0].filled_price == 48.40
    assert broker.positions["acct1"]["AAPL"] == 0.0  # long fully closed by the stop


@pytest.mark.asyncio
async def test_cancel_order_removes_resting_stop(broker_with_long_position):
    broker, account = broker_with_long_position
    stop_result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    cancelled = await broker.cancel_order(account, stop_result.broker_order_id)

    assert cancelled is True
    assert broker.simulate_price("AAPL", 40.00) == []  # nothing left to trigger


@pytest.mark.asyncio
async def test_cancel_order_on_unknown_id_returns_false(broker_with_long_position):
    broker, account = broker_with_long_position
    assert await broker.cancel_order(account, "does-not-exist") is False


@pytest.mark.asyncio
async def test_replace_stop_quantity_resizes_in_place(broker_with_long_position):
    broker, account = broker_with_long_position
    stop_result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    await broker.replace_stop_quantity(account, stop_result.broker_order_id, new_quantity=47.0)

    fills = broker.simulate_price("AAPL", 48.00)
    assert fills[0].filled_quantity == 47.0


@pytest.mark.asyncio
async def test_get_broker_position_reflects_fills(broker_with_long_position):
    broker, account = broker_with_long_position
    assert await broker.get_broker_position(account, "AAPL") == 62.0


@pytest.mark.asyncio
async def test_short_position_gets_buy_stop():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL), account, quantity=30.0, symbol="AAPL"
    )

    await broker.place_protective_stop(account, "AAPL", quantity=30.0, stop_price=52.00, exit_side=Side.BUY)

    assert broker.simulate_price("AAPL", 51.99) == []  # doesn't trigger a BUY-side stop
    fills = broker.simulate_price("AAPL", 52.01)
    assert len(fills) == 1
    assert broker.positions["acct1"]["AAPL"] == 0.0


@pytest.mark.asyncio
async def test_sell_stop_triggers_exactly_at_the_stop_price_not_only_below_it(broker_with_long_position):
    """Docstring: "A SELL stop triggers when price <= stop_price". The
    boundary itself (price == stop_price) must fire -- a `<` instead of
    `<=` would silently leave a long position unprotected at the exact
    stop price."""
    broker, account = broker_with_long_position
    await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    fills = broker.simulate_price("AAPL", 48.50)

    assert len(fills) == 1


@pytest.mark.asyncio
async def test_buy_stop_triggers_exactly_at_the_stop_price_not_only_above_it():
    """Docstring: "a BUY stop triggers when price >= stop_price". Same
    exact-boundary requirement as the SELL-stop case, for a short's
    protective BUY stop."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL), account, quantity=30.0, symbol="AAPL"
    )
    await broker.place_protective_stop(account, "AAPL", quantity=30.0, stop_price=52.00, exit_side=Side.BUY)

    fills = broker.simulate_price("AAPL", 52.00)

    assert len(fills) == 1


@pytest.mark.asyncio
async def test_simulate_price_checks_every_resting_stop_not_only_the_first_mismatched_symbol():
    """A stop resting on an UNRELATED symbol must not short-circuit the
    scan for the symbol actually being priced -- it must be skipped
    (`continue`), not treated as a reason to stop looking at every other
    resting stop (`break`), which would silently leave a different
    symbol's own due stop unfired."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_order(
        Signal(source="test", symbol="MSFT", side=Side.BUY), account, quantity=10.0, symbol="MSFT"
    )
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=62.0, symbol="AAPL"
    )
    # Inserted BEFORE the AAPL stop, on an unrelated symbol -- a `break`
    # on the first symbol mismatch would stop scanning right here.
    await broker.place_protective_stop(account, "MSFT", quantity=10.0, stop_price=300.00, exit_side=Side.SELL)
    await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    fills = broker.simulate_price("AAPL", 48.00)

    assert len(fills) == 1
    assert fills[0].filled_quantity == 62.0
    assert broker.positions["acct1"]["AAPL"] == 0.0
    assert broker.positions["acct1"]["MSFT"] == 10.0  # untouched -- its own stop never triggered


@pytest.mark.asyncio
async def test_get_broker_position_defaults_to_zero_not_one_for_an_untouched_symbol():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    assert await broker.get_broker_position(account, "NEVER_TRADED") == 0.0


@pytest.mark.asyncio
async def test_simulate_price_fill_defaults_to_zero_not_one_for_a_never_recorded_position():
    """A protective stop can be placed on a symbol this broker's
    `positions` book has never recorded a fill for at all (e.g. a
    standalone `place_protective_stop` call with no prior `place_order`
    on that symbol) -- the resulting fill must move the position from a
    real zero baseline, not a fabricated 1.0 one."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_protective_stop(account, "TSLA", quantity=5.0, stop_price=200.00, exit_side=Side.SELL)

    broker.simulate_price("TSLA", 199.00)

    assert broker.positions["acct1"]["TSLA"] == -5.0


@pytest.mark.asyncio
async def test_replace_stop_quantity_applies_the_new_price_not_just_the_quantity(broker_with_long_position):
    """`replace_stop_quantity`'s own docstring: "Resize (and optionally
    reprice)". A caller that passes `new_price` must have the stop
    actually trigger at that new price, not the original one --
    otherwise a repriced protective stop silently keeps protecting at
    the WRONG (stale) level."""
    broker, account = broker_with_long_position
    stop_result = await broker.place_protective_stop(account, "AAPL", quantity=62.0, stop_price=48.50, exit_side=Side.SELL)

    # Doesn't trigger at the ORIGINAL stop price any more.
    await broker.replace_stop_quantity(account, stop_result.broker_order_id, new_quantity=62.0, new_price=45.00)
    assert broker.simulate_price("AAPL", 48.00) == []

    # Triggers at the NEW price.
    fills = broker.simulate_price("AAPL", 44.99)
    assert len(fills) == 1
    assert fills[0].filled_quantity == 62.0


@pytest.mark.asyncio
async def test_each_protective_stop_gets_its_own_distinct_order_id():
    """Two protective stops placed on the same broker (even for the same
    account/symbol pair, as happens on a stop-resize-via-cancel-then-
    resubmit path) must never share a `broker_order_id` -- a collision
    would make the second stop silently overwrite the first in
    `_stop_orders`, leaving only one of the two actually resting."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY), account, quantity=10.0, symbol="AAPL"
    )
    await broker.place_order(
        Signal(source="test", symbol="MSFT", side=Side.BUY), account, quantity=10.0, symbol="MSFT"
    )

    first = await broker.place_protective_stop(account, "AAPL", quantity=10.0, stop_price=90.0, exit_side=Side.SELL)
    second = await broker.place_protective_stop(account, "MSFT", quantity=10.0, stop_price=200.0, exit_side=Side.SELL)

    assert first.broker_order_id != second.broker_order_id
    # Both independently resolvable -- neither silently overwrote the other.
    assert broker.simulate_price("AAPL", 89.0)[0].broker_order_id == first.broker_order_id
    assert broker.simulate_price("MSFT", 199.0)[0].broker_order_id == second.broker_order_id
