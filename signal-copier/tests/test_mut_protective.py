"""Mutation tests for SignalCopierEngine protective level validation and capital exhaustion.

These tests are designed to kill survivor mutations that slipped past the existing
test suite. Each test targets a specific boundary condition or edge case.
"""
from __future__ import annotations

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import AssetClass, DestinationAccount, Intent, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


def _engine(tmp_path, *, account: DestinationAccount | None = None):
    if account is None:
        account = DestinationAccount(account_id="a1", broker="paper", allow_short=True)
    routing = RoutingConfig(
        rules=[RoutingRule(source="s", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    store = SignalStore(tmp_path / "pl.db")
    return SignalCopierEngine(routing=routing, brokers={"paper": PaperBroker()}, store=store)


def _signal(name, side, intent, price, stop=None, target=None):
    return Signal(
        id=f"sig_{name}",
        source="s",
        symbol="AAPL",
        side=side,
        asset_class=AssetClass.EQUITY,
        quantity=10,
        price=price,
        stop_loss=stop,
        take_profit=target,
        intent=intent,
    )


# Tests for boundary conditions on _protective_level_error

@pytest.mark.asyncio
async def test_short_stop_equal_to_price_must_be_rejected(tmp_path):
    """Survivor mutation: short stop < price instead of <= price.

    For shorts, stop_loss must be strictly above the entry price.
    When stop == price, it should still be rejected.
    """
    engine = _engine(tmp_path)
    # Short with stop equal to entry price
    results = await engine.handle_signal(
        _signal("short_stop_equal", Side.SELL, Intent.ENTRY_SHORT, 100.0, 100.0, None),
        dry_run=True,
    )
    assert results
    # This must be rejected - stop can't equal price for shorts
    assert all(r.status == OrderStatus.REJECTED for r in results), \
        "short with stop == price must be rejected"
    assert any("must be above the entry price" in (r.message or "") for r in results)


@pytest.mark.asyncio
async def test_short_target_equal_to_price_must_be_rejected(tmp_path):
    """Survivor mutation: short target > price instead of >= price.

    For shorts, take_profit must be strictly below the entry price.
    When target == price, it should still be rejected.
    """
    engine = _engine(tmp_path)
    # Short with target equal to entry price
    results = await engine.handle_signal(
        _signal("short_target_equal", Side.SELL, Intent.ENTRY_SHORT, 100.0, 105.0, 100.0),
        dry_run=True,
    )
    assert results
    # This must be rejected - target can't equal price for shorts
    assert all(r.status == OrderStatus.REJECTED for r in results), \
        "short with target == price must be rejected"
    assert any("must be below the entry price" in (r.message or "") for r in results)


@pytest.mark.asyncio
async def test_long_target_equal_to_price_must_be_rejected(tmp_path):
    """Ensure target equality check works for longs.

    For longs, take_profit must be strictly above the entry price.
    When target == price, it should still be rejected.
    """
    engine = _engine(tmp_path)
    # Long with target equal to entry price
    results = await engine.handle_signal(
        _signal("long_target_equal", Side.BUY, Intent.ENTRY_LONG, 100.0, 95.0, 100.0),
        dry_run=True,
    )
    assert results
    # This must be rejected - target can't equal price for longs
    assert all(r.status == OrderStatus.REJECTED for r in results), \
        "long with target == price must be rejected"
    assert any("must be above the entry price" in (r.message or "") for r in results)


@pytest.mark.asyncio
async def test_add_buy_side_stop_equality_check(tmp_path):
    """Test that ADD on BUY side correctly validates stop_loss equality.

    ADD on BUY is treated as a long entry, so stop must be strictly below price.
    When stop == price, it should be rejected.
    """
    engine = _engine(tmp_path)
    # ADD on BUY with stop equal to price
    results = await engine.handle_signal(
        _signal("add_buy_stop_equal", Side.BUY, Intent.ADD, 100.0, 100.0, None),
        dry_run=True,
    )
    assert results
    # This must be rejected - stop can't equal price for ADD on BUY
    assert all(r.status == OrderStatus.REJECTED for r in results), \
        "ADD on BUY with stop == price must be rejected"
    assert any("must be below the entry price" in (r.message or "") for r in results)


# Tests for boundary conditions on _account_capital_exhausted

@pytest.mark.asyncio
async def test_broker_none_does_not_exhaust_capital(tmp_path):
    """Survivor mutation: return True instead of False when broker is None.

    When broker is None, _account_capital_exhausted should return False,
    not True. This is documented in the docstring: "Deliberately False for a
    missing adapter or an unreported balance".
    """
    # Create an engine with a broker that doesn't exist
    account = DestinationAccount(account_id="a1", broker="nonexistent")
    routing = RoutingConfig(
        rules=[RoutingRule(source="s", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    store = SignalStore(tmp_path / "pl.db")
    engine = SignalCopierEngine(routing=routing, brokers={"paper": PaperBroker()}, store=store)

    results = await engine.handle_signal(
        _signal("no_broker", Side.BUY, Intent.ENTRY_LONG, 100.0, 95.0, 110.0),
        dry_run=True,
    )
    # Should not reject due to capital exhaustion; will reject for other reasons
    # (missing adapter)
    assert results
    exhaustion_rejections = [r for r in results if "equity" in (r.message or "").lower() and "capital" in (r.message or "").lower()]
    # Should not have a capital exhaustion rejection
    assert not exhaustion_rejections, "missing broker should not trigger capital exhaustion"


@pytest.mark.asyncio
async def test_protective_level_check_on_long_entry(tmp_path):
    """Direct test that protective levels are checked for long entries.

    Ensures that the code path for ENTRY_LONG is correctly identifying long positions.
    """
    engine = _engine(tmp_path)
    # Long entry with stop above price (invalid)
    results = await engine.handle_signal(
        _signal("long_bad", Side.BUY, Intent.ENTRY_LONG, 100.0, 101.0, None),
        dry_run=True,
    )
    assert results
    assert all(r.status == OrderStatus.REJECTED for r in results), \
        "long entry with stop above price must be rejected"
    assert any("must be below" in (r.message or "") for r in results)


@pytest.mark.asyncio
async def test_protective_level_check_on_short_entry(tmp_path):
    """Direct test that protective levels are checked for short entries.

    Ensures that the code path for ENTRY_SHORT is correctly identifying short positions.
    """
    engine = _engine(tmp_path)
    # Short entry with stop below price (invalid)
    results = await engine.handle_signal(
        _signal("short_bad", Side.SELL, Intent.ENTRY_SHORT, 100.0, 99.0, None),
        dry_run=True,
    )
    assert results
    assert all(r.status == OrderStatus.REJECTED for r in results), \
        "short entry with stop below price must be rejected"
    assert any("must be above" in (r.message or "") for r in results)


# Direct unit tests for the three boundaries the first pass left alive.

def _raw(side, intent, price, stop=None, target=None):
    return Signal(source="s", symbol="AAPL", side=side, quantity=1, price=price,
                  stop_loss=stop, take_profit=target, intent=intent)


def test_zero_price_is_never_validated():
    sig = _raw(Side.BUY, Intent.ENTRY_LONG, 0.0, stop=5.0)
    assert SignalCopierEngine._protective_level_error(sig) is None


def test_buy_side_alone_does_not_make_a_non_entry_intent_long():
    # An exit (or any non-entry intent) on the buy side must not be validated as a long entry.
    sig = _raw(Side.BUY, Intent.ENTRY_SHORT, 100.0, stop=105.0)
    assert SignalCopierEngine._protective_level_error(sig) is None
    sig = _raw(Side.SELL, Intent.ADD, 100.0, stop=105.0)
    assert SignalCopierEngine._protective_level_error(sig) is None


@pytest.mark.asyncio
async def test_zero_remaining_capital_counts_as_exhausted(tmp_path):
    from app.models import AccountBalance

    class _Zero(PaperBroker):
        async def get_account_balance(self, account):
            return AccountBalance(account_id=account.account_id, cash=0.0, equity=0.0, buying_power=0.0)

    acct = DestinationAccount(account_id="a1", broker="paper")
    engine = _engine(tmp_path, account=acct)
    engine.brokers["paper"] = _Zero()
    assert await engine._account_capital_exhausted(acct) is True


def test_zero_stop_means_unset_not_a_wrong_side_level():
    sig = _raw(Side.SELL, Intent.ENTRY_SHORT, 100.0, stop=0.0, target=0.0)
    assert SignalCopierEngine._protective_level_error(sig) is None


@pytest.mark.asyncio
async def test_missing_adapter_does_not_count_as_exhausted(tmp_path):
    acct = DestinationAccount(account_id="a1", broker="paper")
    engine = _engine(tmp_path, account=acct)
    engine.brokers.pop("paper")
    assert await engine._account_capital_exhausted(acct) is False


@pytest.mark.asyncio
async def test_unreported_balance_does_not_count_as_exhausted(tmp_path):
    class _NoBal(PaperBroker):
        async def get_account_balance(self, account):
            return None

    acct = DestinationAccount(account_id="a1", broker="paper")
    engine = _engine(tmp_path, account=acct)
    engine.brokers["paper"] = _NoBal()
    assert await engine._account_capital_exhausted(acct) is False
